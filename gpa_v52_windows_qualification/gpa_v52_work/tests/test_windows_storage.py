from pathlib import Path
import json
import subprocess
import sys

import gpa.storage_identity as sid
import gpa.windows_storage as ws


def disk_identity(number, *, bus=ws.BUS_SATA, serial=None, eligible=None, error=None):
    if serial is None:
        serial=f'SERIAL-{number}'
    if eligible is None:
        eligible=bus in ws._DIRECT_LOCAL_BUS_TYPES and bool(serial)
    return ws.PhysicalDiskIdentity(
        disk_number=number,
        bus_type=bus,
        bus_type_name=ws._BUS_TYPE_NAMES.get(bus,f'unknown_{bus}'),
        serial_number=serial,
        descriptor_supported=True,
        hardware_identity_eligible=eligible,
        error=error,
    )


class FakeAPI(ws.WindowsVolumeAPI):
    def __init__(self, *, drive_type=ws.DRIVE_FIXED, extents=None, identities=None):
        self.drive_type=drive_type
        self.extents=tuple(extents or (ws.DiskExtent(3,0,100),))
        numbers={e.disk_number for e in self.extents}
        self.identities=dict(identities or {n:disk_identity(n) for n in numbers})
        self.calls=[]
    def get_volume_path_name(self,path):
        self.calls.append(('path',path));return 'X:\\'
    def get_drive_type(self,mount):
        self.calls.append(('type',mount));return self.drive_type
    def get_volume_name(self,mount):
        self.calls.append(('name',mount));return r'\\?\Volume{TEST}' + '\\'
    def get_disk_extents(self,volume):
        self.calls.append(('extents',volume));return self.extents
    def get_disk_identity(self,number):
        self.calls.append(('identity',number))
        value=self.identities[number]
        if isinstance(value,Exception): raise value
        return value


def topology(disks=(), *, supported=True, bus=ws.BUS_SATA, serial_prefix='SERIAL', identities=None, hardware_supported=None, error=None):
    disks=tuple(disks)
    if identities is None:
        identities=tuple(disk_identity(n,bus=bus,serial=f'{serial_prefix}-{n}') for n in disks) if supported else ()
    if hardware_supported is None:
        hardware_supported=bool(identities) and all(i.hardware_identity_eligible for i in identities)
    return ws.WindowsVolumeTopology(
        path='x',local_volume=supported,topology_supported=supported,
        disk_numbers=disks,disk_identities=tuple(identities),
        hardware_identity_supported=hardware_supported,error=error,
        hardware_identity_error=None if hardware_supported else ('device identity not eligible' if supported else None),
    )


def test_windows_volume_probe_records_extents_and_eligible_device_identities():
    extents=(ws.DiskExtent(7,0,50),ws.DiskExtent(9,50,50),ws.DiskExtent(7,100,50))
    api=FakeAPI(extents=extents,identities={7:disk_identity(7,bus=ws.BUS_SATA),9:disk_identity(9,bus=ws.BUS_NVME)})
    got=ws.probe_windows_volume(Path(r'X:\Archive'),api=api)
    assert got.schema=='gpa.windows-volume-topology.v2'
    assert got.topology_supported and got.local_volume and got.hardware_identity_supported
    assert got.drive_type_name=='fixed' and got.volume_mount_point=='X:\\'
    assert got.disk_numbers==(7,9) and len(got.disk_extents)==3
    assert [i.disk_number for i in got.disk_identities]==[7,9]
    assert [i.serial_number for i in got.disk_identities]==['SERIAL-7','SERIAL-9']
    assert [c for c in api.calls if c[0]=='identity']==[('identity',7),('identity',9)]


def test_windows_volume_probe_virtual_disk_keeps_extent_but_withholds_hardware_proof():
    api=FakeAPI(
        extents=(ws.DiskExtent(3,0,100),),
        identities={3:disk_identity(3,bus=ws.BUS_FILE_BACKED_VIRTUAL,eligible=False,error='file-backed virtual')},
    )
    got=ws.probe_windows_volume(Path(r'X:\Archive'),api=api)
    assert got.topology_supported and got.disk_numbers==(3,)
    assert not got.hardware_identity_supported
    assert got.disk_identities[0].bus_type_name=='file_backed_virtual'


def test_windows_volume_probe_descriptor_failure_fails_hardware_identity_closed_not_extent_evidence():
    api=FakeAPI(extents=(ws.DiskExtent(3,0,100),),identities={3:OSError('descriptor unavailable')})
    got=ws.probe_windows_volume(Path(r'X:\Archive'),api=api)
    assert got.topology_supported and got.disk_numbers==(3,)
    assert not got.hardware_identity_supported
    assert not got.disk_identities[0].descriptor_supported
    assert 'failed closed' in got.disk_identities[0].error



def test_volume_with_duplicate_device_serials_withholds_hardware_identity_proof():
    extents=(ws.DiskExtent(3,0,50),ws.DiskExtent(4,50,50))
    identities={3:disk_identity(3,serial='DUPLICATE'),4:disk_identity(4,serial='duplicate')}
    got=ws.probe_windows_volume(Path(r'X:\Archive'),api=FakeAPI(extents=extents,identities=identities))
    assert got.topology_supported and got.disk_numbers==(3,4)
    assert not got.hardware_identity_supported
    assert 'same storage-device serial' in got.hardware_identity_error

def test_windows_volume_probe_remote_fails_closed_before_volume_management_calls():
    api=FakeAPI(drive_type=ws.DRIVE_REMOTE)
    got=ws.probe_windows_volume(Path(r'Z:\Backup'),api=api)
    assert not got.topology_supported and not got.local_volume
    assert got.drive_type_name=='remote' and 'not supported' in got.error
    assert [c[0] for c in api.calls]==['path','type']


def test_windows_volume_probe_non_windows_without_injected_api_is_unavailable():
    if ws.platform.system()=='Windows': return
    got=ws.probe_windows_volume(Path('/tmp'))
    assert not got.topology_supported and got.disk_numbers==()
    assert got.error=='not running on Windows'


def test_volume_disk_extents_parser_honors_native_structure_alignment():
    count=2;size=ws._EXTENTS_OFFSET+count*ws._EXTENT_SIZE;raw=bytearray(size);raw[:4]=count.to_bytes(4,'little')
    e1=ws._DISK_EXTENT();e1.DiskNumber=4;e1.StartingOffset=123;e1.ExtentLength=456
    e2=ws._DISK_EXTENT();e2.DiskNumber=8;e2.StartingOffset=789;e2.ExtentLength=1011
    raw[ws._EXTENTS_OFFSET:ws._EXTENTS_OFFSET+ws._EXTENT_SIZE]=bytes(e1)
    start=ws._EXTENTS_OFFSET+ws._EXTENT_SIZE;raw[start:start+ws._EXTENT_SIZE]=bytes(e2)
    got=ws._parse_disk_extents_buffer(bytes(raw))
    assert [(e.disk_number,e.starting_offset,e.extent_length) for e in got]==[(4,123,456),(8,789,1011)]


def test_volume_disk_extents_parser_rejects_truncation():
    raw=(2).to_bytes(4,'little')+b'\0'*8
    try: ws._parse_disk_extents_buffer(raw)
    except ValueError as exc: assert 'truncated' in str(exc) or 'too short' in str(exc)
    else: raise AssertionError('truncated disk-extents buffer was accepted')


def _descriptor_bytes(*,bus=ws.BUS_NVME,serial='NVME-ABC',vendor='VENDOR',product='MODEL',revision='1.0'):
    fixed=ws._STORAGE_DEVICE_DESCRIPTOR_FIXED();base=ws._DEVICE_DESCRIPTOR_MIN_SIZE
    payload=bytearray(bytes(fixed));parts=[]
    def add(text):
        nonlocal payload
        if text is None:return 0
        off=len(payload);payload.extend(text.encode('ascii')+b'\0');return off
    vendor_off=add(vendor);product_off=add(product);revision_off=add(revision);serial_off=add(serial)
    fixed.Version=base;fixed.Size=len(payload);fixed.DeviceType=0;fixed.DeviceTypeModifier=0;fixed.RemovableMedia=0;fixed.CommandQueueing=1
    fixed.VendorIdOffset=vendor_off;fixed.ProductIdOffset=product_off;fixed.ProductRevisionOffset=revision_off;fixed.SerialNumberOffset=serial_off
    fixed.BusType=bus;fixed.RawPropertiesLength=0
    payload[:base]=bytes(fixed)
    return bytes(payload)


def test_storage_device_descriptor_parser_records_bus_serial_and_eligibility():
    got=ws._parse_storage_device_descriptor(_descriptor_bytes(bus=ws.BUS_NVME,serial='ABC123'),5)
    assert got.schema=='gpa.windows-physical-disk-identity.v1'
    assert got.disk_number==5 and got.bus_type_name=='nvme' and got.serial_number=='ABC123'
    assert got.vendor_id=='VENDOR' and got.product_id=='MODEL' and got.product_revision=='1.0'
    assert got.descriptor_supported and got.hardware_identity_eligible and got.error is None


def test_storage_device_descriptor_virtual_and_missing_serial_are_not_eligible():
    virtual=ws._parse_storage_device_descriptor(_descriptor_bytes(bus=ws.BUS_VIRTUAL,serial='VHD-1'),1)
    assert not virtual.hardware_identity_eligible and 'not eligible' in virtual.error
    missing=ws._parse_storage_device_descriptor(_descriptor_bytes(bus=ws.BUS_SATA,serial=None),2)
    assert not missing.hardware_identity_eligible and 'no usable serial' in missing.error


def test_storage_device_descriptor_parser_rejects_bad_string_offset():
    raw=bytearray(_descriptor_bytes());fixed=ws._STORAGE_DEVICE_DESCRIPTOR_FIXED.from_buffer_copy(raw[:ws._DEVICE_DESCRIPTOR_MIN_SIZE])
    fixed.SerialNumberOffset=len(raw)+100;raw[:ws._DEVICE_DESCRIPTOR_MIN_SIZE]=bytes(fixed)
    try: ws._parse_storage_device_descriptor(bytes(raw),0)
    except ValueError as exc: assert 'outside' in str(exc)
    else: raise AssertionError('invalid descriptor offset was accepted')


def test_windows_storage_identity_direct_disjoint_devices_are_machine_proven(tmp_path,monkeypatch):
    a=tmp_path/'primary';b=tmp_path/'backup';a.mkdir();b.mkdir()
    monkeypatch.setattr(sid.platform,'system',lambda:'Windows')
    monkeypatch.setattr(sid,'_device_token',lambda p:'vol-a' if Path(p)==a else 'vol-b')
    monkeypatch.setattr(sid,'_windows_volume_topology',lambda p:topology((1,),bus=ws.BUS_SATA,serial_prefix='A') if Path(p)==a else topology((4,),bus=ws.BUS_NVME,serial_prefix='B'))
    r=sid.assess_storage_independence(a,b)
    assert r.schema=='gpa.storage-identity.v4'
    assert r.separate_volume_proven and r.separate_disk_numbers_proven
    assert r.same_physical_device is False and r.separate_physical_device_proven
    assert r.primary_physical_serials==('A-1',) and r.secondary_physical_serials==('B-4',)
    assert r.separate_physical_media_confirmed and not r.separate_physical_media_confirmation_requested
    assert r.independence_evidence_source=='windows_disk_extents_and_storage_device_descriptor'


def test_disjoint_virtual_disk_numbers_do_not_auto_prove_physical_hardware(tmp_path,monkeypatch):
    a=tmp_path/'primary';b=tmp_path/'backup';a.mkdir();b.mkdir()
    monkeypatch.setattr(sid.platform,'system',lambda:'Windows')
    monkeypatch.setattr(sid,'_device_token',lambda p:'vol-a' if Path(p)==a else 'vol-b')
    va=disk_identity(1,bus=ws.BUS_FILE_BACKED_VIRTUAL,serial='VHD-A',eligible=False,error='file-backed virtual')
    vb=disk_identity(4,bus=ws.BUS_FILE_BACKED_VIRTUAL,serial='VHD-B',eligible=False,error='file-backed virtual')
    monkeypatch.setattr(sid,'_windows_volume_topology',lambda p:topology((1,),identities=(va,),hardware_supported=False) if Path(p)==a else topology((4,),identities=(vb,),hardware_supported=False))
    r=sid.assess_storage_independence(a,b)
    assert r.separate_disk_numbers_proven and not r.separate_physical_device_proven
    assert not r.separate_physical_media_confirmed
    assert 'disk numbers alone do not prove' in r.note
    confirmed=sid.assess_storage_independence(a,b,separate_physical_media_confirmed=True)
    assert confirmed.separate_physical_media_confirmed
    assert confirmed.independence_evidence_source=='explicit_user_or_operator_input'


def test_disjoint_disk_numbers_with_missing_serial_do_not_auto_prove(tmp_path,monkeypatch):
    a=tmp_path/'primary';b=tmp_path/'backup';a.mkdir();b.mkdir()
    monkeypatch.setattr(sid.platform,'system',lambda:'Windows');monkeypatch.setattr(sid,'_device_token',lambda p:'a' if Path(p)==a else 'b')
    ia=disk_identity(1,bus=ws.BUS_SATA,serial='',eligible=False,error='no serial')
    ib=disk_identity(2,bus=ws.BUS_NVME,serial='B-2')
    monkeypatch.setattr(sid,'_windows_volume_topology',lambda p:topology((1,),identities=(ia,),hardware_supported=False) if Path(p)==a else topology((2,),identities=(ib,)))
    r=sid.assess_storage_independence(a,b)
    assert r.separate_disk_numbers_proven and not r.separate_physical_device_proven
    assert not r.separate_physical_media_confirmed


def test_distinct_disk_numbers_with_same_serial_are_identity_conflict_and_block_confirmation(tmp_path,monkeypatch):
    a=tmp_path/'primary';b=tmp_path/'backup';a.mkdir();b.mkdir()
    monkeypatch.setattr(sid.platform,'system',lambda:'Windows');monkeypatch.setattr(sid,'_device_token',lambda p:'a' if Path(p)==a else 'b')
    monkeypatch.setattr(sid,'_windows_volume_topology',lambda p:topology((1,),serial_prefix='DUP') if Path(p)==a else topology((2,),identities=(disk_identity(2,serial='DUP-1'),)))
    r=sid.assess_storage_independence(a,b,separate_physical_media_confirmed=True)
    assert r.separate_disk_numbers_proven and r.physical_identity_conflict
    assert not r.separate_physical_device_proven and not r.separate_physical_media_confirmed
    assert 'serial' in r.note.lower() and 'blocked' in r.note.lower()


def test_windows_storage_identity_different_partitions_same_disk_block_confirmation(tmp_path,monkeypatch):
    a=tmp_path/'primary';b=tmp_path/'backup';a.mkdir();b.mkdir()
    monkeypatch.setattr(sid.platform,'system',lambda:'Windows');monkeypatch.setattr(sid,'_device_token',lambda p:'partition-a' if Path(p)==a else 'partition-b')
    monkeypatch.setattr(sid,'_windows_volume_topology',lambda p:topology((7,),serial_prefix='SAME'))
    r=sid.assess_storage_independence(a,b,separate_physical_media_confirmed=True)
    assert r.separate_volume_proven and r.same_physical_device is True and not r.separate_physical_device_proven
    assert r.separate_physical_media_confirmation_requested and not r.separate_physical_media_confirmed
    assert r.independence_evidence_source is None and 'cannot override' in r.note


def test_windows_storage_identity_spanned_volume_overlap_blocks_independence(tmp_path,monkeypatch):
    a=tmp_path/'primary';b=tmp_path/'backup';a.mkdir();b.mkdir()
    monkeypatch.setattr(sid.platform,'system',lambda:'Windows');monkeypatch.setattr(sid,'_device_token',lambda p:'volume-a' if Path(p)==a else 'volume-b')
    monkeypatch.setattr(sid,'_windows_volume_topology',lambda p:topology((0,1),serial_prefix='A') if Path(p)==a else topology((1,2),serial_prefix='B'))
    r=sid.assess_storage_independence(a,b,separate_physical_media_confirmed=True)
    assert r.same_physical_device is True and not r.separate_physical_media_confirmed
    assert r.primary_physical_disks==(0,1) and r.secondary_physical_disks==(1,2) and '[1]' in r.note


def test_windows_remote_or_unsupported_topology_never_auto_proves_physical_independence(tmp_path,monkeypatch):
    a=tmp_path/'primary';b=tmp_path/'backup';a.mkdir();b.mkdir()
    monkeypatch.setattr(sid.platform,'system',lambda:'Windows');monkeypatch.setattr(sid,'_device_token',lambda p:'volume-a' if Path(p)==a else 'remote-volume')
    monkeypatch.setattr(sid,'_windows_volume_topology',lambda p:topology((2,),serial_prefix='A') if Path(p)==a else topology((),supported=False,error='remote/network volume'))
    r=sid.assess_storage_independence(a,b)
    assert r.separate_volume_proven and r.same_physical_device is None and not r.separate_physical_device_proven and not r.separate_physical_media_confirmed
    assert r.secondary_windows_topology['error']=='remote/network volume'
    confirmed=sid.assess_storage_independence(a,b,separate_physical_media_confirmed=True)
    assert confirmed.separate_physical_media_confirmed and confirmed.independence_evidence_source=='explicit_user_or_operator_input'


def test_ioctl_constants_match_windows_storage_control_codes():
    assert ws.IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS==0x00560000
    assert ws.IOCTL_STORAGE_QUERY_PROPERTY==0x002D1400


def test_bus_types_explicitly_distinguish_virtual_network_and_spaces():
    assert ws._BUS_TYPE_NAMES[ws.BUS_ISCSI]=='iscsi'
    assert ws._BUS_TYPE_NAMES[ws.BUS_VIRTUAL]=='virtual'
    assert ws._BUS_TYPE_NAMES[ws.BUS_FILE_BACKED_VIRTUAL]=='file_backed_virtual'
    assert ws._BUS_TYPE_NAMES[ws.BUS_SPACES]=='storage_spaces'
    assert {ws.BUS_ISCSI,ws.BUS_VIRTUAL,ws.BUS_FILE_BACKED_VIRTUAL,ws.BUS_SPACES,ws.BUS_RAID}.isdisjoint(ws._DIRECT_LOCAL_BUS_TYPES)


def test_windows_storage_qualification_cli_fails_closed_off_windows(tmp_path):
    if ws.platform.system()=='Windows': return
    root=Path(__file__).resolve().parents[1];a=tmp_path/'a';b=tmp_path/'b';a.mkdir();b.mkdir()
    cp=subprocess.run([sys.executable,str(root/'tools'/'qualify_windows_storage.py'),str(a),str(b)],cwd=root,capture_output=True,text=True,check=False)
    assert cp.returncode==1
    obj=json.loads(cp.stdout)
    assert obj['schema']=='gpa.windows-storage-qualification.v2'
    assert not obj['passed'] and obj['result']=='physical_device_independence_unproven'
    assert obj['assessment']['schema']=='gpa.storage-identity.v4'

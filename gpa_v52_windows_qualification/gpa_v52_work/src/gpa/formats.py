from __future__ import annotations

# Google Photos current backup/transfer families (2026-08) plus conservative
# RAW extensions commonly produced by camera models Google documents as supported.
# Unknown extensions are preserved byte-for-byte by the unclassified-material lane;
# extending these sets must never be required to avoid data loss.
PHOTO_STANDARD_SUFFIXES = {
    '.jpg', '.jpeg', '.heic', '.heif', '.png', '.webp', '.gif', '.avif',
}

RAW_SUFFIXES = {
    '.dng', '.cr2', '.cr3', '.crw', '.nef', '.nrw', '.arw', '.sr2', '.srf',
    '.raf', '.rw2', '.rwl', '.orf', '.pef', '.srw', '.3fr', '.erf', '.mef',
    '.mos', '.mrw', '.x3f', '.iiq', '.raw',
}

VIDEO_SUFFIXES = {
    '.mpg', '.mpeg', '.mod', '.mmv', '.tod', '.wmv', '.asf', '.avi', '.divx',
    '.mov', '.m4v', '.3gp', '.3g2', '.mp4', '.m2t', '.m2ts', '.mts', '.mkv',
}

MEDIA_SUFFIXES = PHOTO_STANDARD_SUFFIXES | RAW_SUFFIXES | VIDEO_SUFFIXES

# Formats our lightweight, no-ExifTool still reader can inspect conservatively.
PILLOW_STILL_SUFFIXES = {'.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff'}

# Apple Live Photo pairing uses QuickTime content identifiers; keeping this
# narrower than VIDEO_SUFFIXES prevents generic movies from being treated as
# candidate Live Photo components merely because ffprobe can read them.
LIVE_PHOTO_STILL_SUFFIXES = {'.jpg', '.jpeg', '.heic', '.heif', '.avif'}
LIVE_PHOTO_MOVIE_SUFFIXES = {'.mov', '.mp4', '.m4v'}

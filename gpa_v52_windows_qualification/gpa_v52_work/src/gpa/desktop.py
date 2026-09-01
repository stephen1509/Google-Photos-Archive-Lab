from __future__ import annotations

"""Thin Tk desktop shell for the product workflow.

Preservation logic deliberately lives in gpa.product and the existing engine.  This
module only renders state and launches read-only refreshes, making it practical to
test safety decisions without requiring a graphical display in CI.
"""

from pathlib import Path
from .product import load_product_session, build_product_dashboard


def dashboard_rows(dashboard):
    rows=[]
    for key,value in dashboard.qualification_gates.items():
        rows.append((key,'PASS' if value else 'BLOCKED'))
    return rows


def launch(session_path:Path)->None:
    import tkinter as tk
    from tkinter import ttk, messagebox

    session_path=Path(session_path)
    root=tk.Tk();root.title('Google Photos Archive Lab');root.geometry('920x680');root.minsize(760,560)
    outer=ttk.Frame(root,padding=18);outer.pack(fill='both',expand=True)
    ttk.Label(outer,text='Google Photos Archive Lab',font=('Segoe UI',18,'bold')).pack(anchor='w')
    ttk.Label(outer,text='Preservation-first migration dashboard',font=('Segoe UI',10)).pack(anchor='w',pady=(0,14))
    state_var=tk.StringVar(value='Loading…')
    ttk.Label(outer,textvariable=state_var,font=('Segoe UI',14,'bold')).pack(anchor='w',pady=(0,12))
    tree=ttk.Treeview(outer,columns=('gate','state'),show='headings',height=12)
    tree.heading('gate',text='Safety gate');tree.heading('state',text='State')
    tree.column('gate',width=560,anchor='w');tree.column('state',width=180,anchor='center');tree.pack(fill='both',expand=True)
    details=tk.Text(outer,height=9,wrap='word');details.pack(fill='x',pady=(12,0))
    details.configure(state='disabled')

    def refresh():
        try:d=build_product_dashboard(load_product_session(session_path))
        except Exception as e:
            messagebox.showerror('Refresh failed',str(e));return
        for i in tree.get_children():tree.delete(i)
        for gate,status in dashboard_rows(d):tree.insert('', 'end', values=(gate.replace('_',' ').title(),status))
        state_var.set('Google retirement: '+d.retirement_state.replace('_',' '))
        details.configure(state='normal');details.delete('1.0','end')
        if d.blockers:details.insert('end','Blocking items:\n• '+'\n• '.join(d.blockers))
        else:details.insert('end','All automated gates pass. Final human review is still required before deleting cloud originals.')
        details.configure(state='disabled')
    ttk.Button(outer,text='Refresh evidence',command=refresh).pack(anchor='e',pady=(12,0))
    refresh();root.mainloop()

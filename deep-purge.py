#!/usr/bin/env python3
import os
import sys
import glob
import subprocess
import threading
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango

# --- 1. System Inventory Harvesting ---
def harvest_applications():
    items = []
    seen_names = set()

    # Flatpaks
    try:
        res = subprocess.run(["flatpak", "list", "--app", "--columns=name,application"],
                             capture_output=True, text=True, check=False)
        for line in res.stdout.strip().splitlines():
            parts = line.split('\t')
            if len(parts) >= 2:
                items.append(("Flatpak", parts[0].strip(), parts[1].strip()))
    except FileNotFoundError:
        pass

    # Snaps
    try:
        res = subprocess.run(["snap", "list"], capture_output=True, text=True, check=False)
        for line in res.stdout.strip().splitlines()[1:]:
            parts = line.split()
            if parts:
                name = parts[0]
                if not (name.startswith("core") or name in ["snapd", "bare", "gtk-common-themes"] or name.startswith("gnome-")):
                    items.append(("Snap", name, name))
    except FileNotFoundError:
        pass

    # AppImages
    home = os.path.expanduser("~")
    search_dirs = [home, os.path.join(home, "Applications"), os.path.join(home, "Downloads"), "/opt", os.path.join(home, ".local/bin")]
    for sdir in search_dirs:
        if os.path.isdir(sdir):
            for f in glob.glob(os.path.join(sdir, "*.AppImage")):
                clean_name = os.path.splitext(os.path.basename(f))[0]
                items.append(("AppImage", clean_name, f))

    # APT / Native Desktop Launchers
    desktop_dirs = ["/usr/share/applications", os.path.expanduser("~/.local/share/applications")]
    for ddir in desktop_dirs:
        if not os.path.isdir(ddir):
            continue
        for dfile in glob.glob(os.path.join(ddir, "*.desktop")):
            name = None
            try:
                with open(dfile, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if line.startswith("Name=") and not name:
                            name = line.strip().split("=", 1)[1]
                            break
            except Exception:
                continue

            if name and name not in seen_names:
                seen_names.add(name)
                dpkg_res = subprocess.run(["dpkg", "-S", dfile], capture_output=True, text=True, check=False)
                if dpkg_res.returncode == 0:
                    pkg = dpkg_res.stdout.split(":")[0].strip()
                    items.append(("APT/Deb", name, pkg))
                else:
                    base_id = os.path.splitext(os.path.basename(dfile))[0]
                    items.append(("Custom/Local", name, base_id))

    return sorted(items, key=lambda x: x[1].lower())

# --- 2. Purge Backend Logic ---
def execute_purge(backend, name, target_id):
    script = f"""#!/usr/bin/env bash
systemctl --user stop "{name}" "{target_id}" 2>/dev/null
pkill -9 -f "{target_id}" 2>/dev/null
pkill -9 -f "{name}" 2>/dev/null

case "{backend}" in
    "Flatpak")
        flatpak uninstall --delete-data -y "{target_id}"
        rm -rf ~/.var/app/"{target_id}"*
        ;;
    "Snap")
        pkexec snap remove --purge "{target_id}"
        ;;
    "AppImage")
        rm -f "{target_id}"
        ;;
    "APT/Deb")
        pkexec apt-get purge --auto-remove -y "{target_id}"
        ;;
    "Custom/Local")
        pkexec rm -f "/usr/local/bin/{target_id}" "/usr/bin/{target_id}"
        rm -f "$HOME/.local/bin/{target_id}"
        ;;
esac

CLEAN_ID=$(basename "{target_id}" | tr -cd '[:alnum:]_-')
if [ ${{#CLEAN_ID}} -ge 3 ]; then
    rm -rf ~/.config/*"$CLEAN_ID"* ~/.local/share/*"$CLEAN_ID"* ~/.cache/*"$CLEAN_ID"*
    rm -f ~/.local/share/applications/*"$CLEAN_ID"*.desktop
    rm -f ~/.config/autostart/*"$CLEAN_ID"*.desktop
    rm -f ~/.config/systemd/user/*"$CLEAN_ID"*.service
    pkexec bash -c "rm -rf /usr/share/applications/*$CLEAN_ID*.desktop /usr/local/share/applications/*$CLEAN_ID*.desktop /usr/share/icons/*/*/*$CLEAN_ID*"
fi
systemctl --user daemon-reload
update-desktop-database ~/.local/share/applications 2>/dev/null
pkexec update-desktop-database /usr/share/applications 2>/dev/null
"""
    subprocess.run(["bash", "-c", script], check=False)

# --- 3. Native Zorin GTK3 UI ---
class DeepPurgeWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="DeepPurge")
        self.set_default_size(840, 560)
        self.set_position(Gtk.WindowPosition.CENTER)

        # Force dark mode preference if available
        settings = Gtk.Settings.get_default()
        settings.set_property("gtk-application-prefer-dark-theme", True)

        # HeaderBar (Zorin / GNOME style)
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.props.title = "DeepPurge"
        header.props.subtitle = "System-Wide Software Uninstaller"
        self.set_titlebar(header)

        # Main Layout Container
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        main_box.set_margin_top(12)
        main_box.set_margin_bottom(12)
        main_box.set_margin_start(16)
        main_box.set_margin_end(16)
        self.add(main_box)

        # Search Entry Bar
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search by application name, backend, or identifier...")
        self.search_entry.connect("search-changed", self.on_search_changed)
        main_box.pack_start(self.search_entry, False, False, 0)

        # Scrolled View + TreeView Table
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_shadow_type(Gtk.ShadowType.IN)
        main_box.pack_start(scrolled, True, True, 0)

        # ListStore Model: Backend, App Name, Target ID
        self.model = Gtk.ListStore(str, str, str)
        self.filter_model = self.model.filter_new()
        self.filter_model.set_visible_func(self.filter_rows)

        self.treeview = Gtk.TreeView(model=self.filter_model)
        self.treeview.set_rules_hint(True)
        self.treeview.connect("row-activated", self.on_row_double_click)

        # Columns
        backends = [("Backend", 0, 120), ("Application Name", 1, 280), ("Identifier / Path", 2, 380)]
        for title, col_idx, width in backends:
            renderer = Gtk.CellRendererText()
            renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
            col = Gtk.TreeViewColumn(title, renderer, text=col_idx)
            col.set_resizable(True)
            col.set_sort_column_id(col_idx)
            if width:
                col.set_min_width(width)
            self.treeview.append_column(col)

        scrolled.add(self.treeview)

        # Bottom Bar: Status Label and Destructive Purge Button
        bottom_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        main_box.pack_start(bottom_bar, False, False, 0)

        self.status_label = Gtk.Label(label="Scanning system backends...")
        self.status_label.set_xalign(0.0)
        bottom_bar.pack_start(self.status_label, True, True, 0)

        self.purge_btn = Gtk.Button(label="Purge Application")
        self.purge_btn.get_style_context().add_class("destructive-action")
        self.purge_btn.connect("clicked", self.on_purge_clicked)
        bottom_bar.pack_end(self.purge_btn, False, False, 0)

        self.show_all()
        GLib.idle_add(self.load_inventory_async)

    def load_inventory_async(self):
        def worker():
            apps = harvest_applications()
            GLib.idle_add(self.populate_data, apps)
        threading.Thread(target=worker, daemon=True).start()

    def populate_data(self, apps):
        self.model.clear()
        for row in apps:
            self.model.append(list(row))
        self.status_label.set_text(f"Total Applications Detected: {len(apps)}")

    def filter_rows(self, model, iter, data):
        query = self.search_entry.get_text().strip().lower()
        if not query:
            return True
        backend = model.get_value(iter, 0).lower()
        name = model.get_value(iter, 1).lower()
        ident = model.get_value(iter, 2).lower()
        return query in backend or query in name or query in ident

    def on_search_changed(self, entry):
        self.filter_model.refilter()
        count = len(self.filter_model)
        total = len(self.model)
        self.status_label.set_text(f"Matching Applications: {count} of {total}")

    def on_row_double_click(self, treeview, path, column):
        self.on_purge_clicked(None)

    def on_purge_clicked(self, button):
        selection = self.treeview.get_selection()
        model, tree_iter = selection.get_selected()
        if not tree_iter:
            msg = Gtk.MessageDialog(
                transient_for=self,
                flags=0,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text="Selection Required"
            )
            msg.format_secondary_text("Please click on an application in the list before purging.")
            msg.run()
            msg.destroy()
            return

        backend = model.get_value(tree_iter, 0)
        name = model.get_value(tree_iter, 1)
        ident = model.get_value(tree_iter, 2)

        confirm = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Purge {name}?"
        )
        confirm.format_secondary_markup(
            f"Are you sure you want to completely destroy:\n\n"
            f"<b>{name}</b> ({backend})\n"
            f"Identifier: <i>{ident}</i>\n\n"
            f"This will stop services, remove system/container packages, and scrub local configs, caches, models, and desktop launchers."
        )
        response = confirm.run()
        confirm.destroy()

        if response == Gtk.ResponseType.YES:
            self.status_label.set_text(f"Excising {name}...")
            self.purge_btn.set_sensitive(False)

            def purge_thread():
                execute_purge(backend, name, ident)
                GLib.idle_add(self.on_purge_finished, name)

            threading.Thread(target=purge_thread, daemon=True).start()

    def on_purge_finished(self, name):
        self.purge_btn.set_sensitive(True)
        done_dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="Purge Complete"
        )
        done_dialog.format_secondary_text(f"'{name}' and all associated residue have been completely excised.")
        done_dialog.run()
        done_dialog.destroy()
        self.load_inventory_async()

if __name__ == "__main__":
    app = DeepPurgeWindow()
    app.connect("destroy", Gtk.main_quit)
    Gtk.main()

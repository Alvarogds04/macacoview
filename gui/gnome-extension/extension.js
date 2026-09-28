import St from 'gi://St';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Clutter from 'gi://Clutter';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const BAR = GLib.getenv('HOME') + '/.local/bin/macacoview-bar';
const SERVICE = 'pc-ai-monitor-gnome.service';
const LAUNCHER =
    GLib.getenv('HOME') + '/.local/bin/pc-ai-monitor-gnome';
const PILL_WIDTH = 46; // px, fixed RAM track width

export default class PcAiMonitorExtension extends Extension {

    enable() {

        this._indicator = new PanelMenu.Button(
            0.0,
            'PC-AI Monitor',
            false
        );

        // Pill container
        this._pillBox = new St.BoxLayout({
            style_class: 'pc-ai-pill',
            y_align: Clutter.ActorAlign.CENTER
        });

        // RAM track. A horizontal box, not a St.Bin: a Bin stretches its
        // single child to the whole allocation, which would always render a
        // full bar and hide real usage. Inside a box the fill keeps its own
        // width and the remaining track stays visible.
        this._track = new St.BoxLayout({
            style_class: 'pc-ai-pill-track',
            width: PILL_WIDTH,
            height: 8,
            y_align: Clutter.ActorAlign.CENTER
        });

        // Filled portion, resized on every update
        this._fill = new St.Widget({
            style_class: 'pc-ai-pill-fill',
            width: 0,
            height: 8
        });
        this._track.add_child(this._fill);

        this._pillBox.add_child(this._track);

        // Used RAM label
        this._ramLabel = new St.Label({
            text: '0.0G',
            style_class: 'pc-ai-pill-ram',
            y_align: Clutter.ActorAlign.CENTER
        });
        this._pillBox.add_child(this._ramLabel);

        // Separator
        this._sep = new St.Label({
            text: '|',
            style_class: 'pc-ai-pill-sep',
            y_align: Clutter.ActorAlign.CENTER
        });
        this._pillBox.add_child(this._sep);

        // Chips label
        this._chipsLabel = new St.Label({
            text: '🖥 …',
            style_class: 'pc-ai-pill-chips',
            y_align: Clutter.ActorAlign.CENTER
        });
        this._pillBox.add_child(this._chipsLabel);

        this._indicator.add_child(this._pillBox);

        const openItem =
            new PopupMenu.PopupMenuItem(
                '📊 Abrir PC-AI Monitor'
            );

        openItem.connect('activate', () => this._openApp());

        this._indicator.menu.addMenuItem(openItem);

        // Una unica accion no justifica un menu previo: el clic izquierdo
        // abre la app. El menu queda para el boton secundario.
        this._indicator.connect('button-press-event', (_actor, event) => {
            if (event.get_button() !== 1) {
                return Clutter.EVENT_PROPAGATE;
            }
            this._openApp();
            return Clutter.EVENT_STOP;
        });

        Main.panel.addToStatusArea(
            'pc-ai-monitor',
            this._indicator,
            1,
            'right'
        );

        this._running = true;
        this._activeCall = null;
        this._update();

        // cada 1 segundo
        this._timer = GLib.timeout_add_seconds(
            GLib.PRIORITY_DEFAULT,
            1,
            () => {
                this._update();
                return GLib.SOURCE_CONTINUE;
            }
        );
    }

    _openApp() {
        // gnome-shell no exporta WAYLAND_DISPLAY ni DBUS_SESSION_BUS_ADDRESS
        // a sus hijos: un spawn directo muere sin dejar rastro visible.
        // systemd --user vive dentro de la sesion de login y ahi la app si ve
        // el compositor; el lanzador directo queda como ultimo recurso.
        try {
            Gio.Subprocess.new(
                ['systemctl', '--user', 'start', SERVICE],
                Gio.SubprocessFlags.NONE
            );
            return;
        } catch (e) {
            console.error(`pc-ai-monitor: systemd fallo: ${e.message}`);
        }

        try {
            Gio.Subprocess.new(
                [LAUNCHER],
                Gio.SubprocessFlags.STDOUT_IGNORE |
                    Gio.SubprocessFlags.STDERR_IGNORE
            );
        } catch (e) {
            console.error(`pc-ai-monitor: no se pudo abrir: ${e.message}`);
        }
    }

    _update() {

        // Guard: at most one in-flight call at a time
        if (this._activeCall) {
            return;
        }

        try {

            const proc = Gio.Subprocess.new(
                [BAR, '--pill'],
                Gio.SubprocessFlags.STDOUT_PIPE
            );

            // Keep a ref so we can check identity later
            this._activeCall = proc;

            // g_subprocess_communicate_utf8_async(stdin_buf, cancellable, callback).
            // Passing an io priority as the second argument throws:
            // "Expected an object of type GCancellable ... but got type number".
            proc.communicate_utf8_async(
                null, // stdin_buf
                null, // cancellable
                (source, result) => {

                    // Ignore if we're already disabled
                    if (!this._running) {
                        return;
                    }

                    // Ignore if a newer call has superseded this one
                    if (this._activeCall !== source) {
                        return;
                    }

                    this._activeCall = null;

                    try {

                        const [ok, stdout] =
                            source.communicate_utf8_finish(result);

                        if (ok && stdout) {

                            const line = stdout.trim();
                            const parts = line.split('|');

                            if (parts.length === 3) {

                                const used = parseFloat(parts[0]);
                                const total = parseFloat(parts[1]);
                                const chips = parts[2];

                                if (
                                    Number.isFinite(used) &&
                                    Number.isFinite(total) &&
                                    total > 0
                                ) {

                                    const fillWidth = Math.max(
                                        0,
                                        Math.min(
                                            PILL_WIDTH,
                                            Math.round(
                                                PILL_WIDTH * used / total
                                            )
                                        )
                                    );
                                    this._fill.width = fillWidth;
                                    this._ramLabel.text =
                                        `${used.toFixed(1)}G`;
                                    this._chipsLabel.text = chips;

                                    return;
                                }
                            }

                            // Fallback: raw output
                            this._setPlaceholder(line || '🖥 ?');

                        } else {

                            this._setPlaceholder('🖥 ?');
                        }

                    } catch (e) {

                        console.error(e);
                        this._setPlaceholder('🖥 !');
                    }

                }
            );

        } catch (e) {

            console.error(e);
            this._setPlaceholder('🖥 !');
            this._activeCall = null;
        }
    }

    _setPlaceholder(text) {
        this._fill.width = 0;
        this._ramLabel.text = '0.0G';
        this._chipsLabel.text = text;
    }

    disable() {

        this._running = false;

        if (this._timer) {
            GLib.source_remove(this._timer);
            this._timer = null;
        }

        this._activeCall = null;

        if (this._indicator) {
            this._indicator.destroy();
            this._indicator = null;
        }

        this._pillBox = null;
        this._track = null;
        this._fill = null;
        this._ramLabel = null;
        this._sep = null;
        this._chipsLabel = null;
    }
}

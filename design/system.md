# MacacoView design system

Synced read-only from the app's real styling source. Every value below is found in a source path;
nothing here is invented. Where a source value fails a contrast check, it is flagged, not replaced.

Sources read:

- `gui/pc_ai_monitor/theme.py` — `THEMES["gentle"]` palette (default theme), `BASE_CSS`, `colors_css()`.
- `odd/tasks/macacoview-gtk.md` L25 — the user's choice of the three graphite surfaces
  (`background`, `element`, `subtle`), owned by the app (`APP_OWNED_FIELDS`) so the gentle-pi refresh
  never brings back `#06080f`.
- `~/.pi/agent/npm/node_modules/gentle-pi/themes/Gentle.json` — only to confirm that `load_external()`
  refreshes the non-surface fields with the same values (`muted` = `dim` = `#5C6170`).

Platform: GTK4 + libadwaita, dark only. Components must map to GTK/Adw widgets
(`Gtk.ProgressBar`, `Gtk.Switch` / `Adw.SwitchRow`, `Gtk.Spinner`, `Adw.Toast`, `Adw.AlertDialog`,
`Adw.ActionRow` / `Adw.ExpanderRow`, `Gtk.ListBox`).

## Colors — Gentle theme (`theme.py` `THEMES["gentle"]`, exposed as `@theme_*` by `colors_css`)

| Token | Value | Usage | Source |
|---|---|---|---|
| `background` / `@theme_bg` | `#17191e` | Window, headerbar, page background | theme.py (L25 graphite) |
| `element` / `@theme_element` | `#1e2128` | `.card`, `.kpi`, `.switch-pill` surface | theme.py (L25 graphite) |
| `subtle` / `@theme_subtle` | `#24272f` | `.chip`, `.kbd`, `.age-pill`, disabled `suggested`/`destructive` buttons | theme.py (L25 graphite) |
| `border` / `@theme_border` | `#313342` | Card hairlines, separators, scrollbar slider, chip outline | theme.py |
| `text` / `@theme_text` | `#F3F6F9` | Primary text, values, `.model-name`, `.row-name` | theme.py |
| `muted` / `@theme_muted` | `#5C6170` | `.card-title`, `.muted`, `.kpi-label`, `.section-head`, `.kbd` text, sidebar badges | theme.py, Gentle.json |
| `accent` / `@theme_accent` | `#E0C15A` | `suggested-action`, progress fill, selected sidebar row (inset bar + label), `.accent` | theme.py |
| `blue` / `@theme_blue` | `#7FB4CA` | `progressbar.bar-blue`, `progressbar.inv-ctx` (context bars) | theme.py |
| `green` / `@theme_green` | `#B7CC85` | OK state: `.tier-ok`, `.age-ok`, `.vpill-ok`, `bar-green` | theme.py |
| `warning` / `@theme_warning` | `#DEBA87` | Stale / attention: `.tier-warn`, `.age-warn`, `.chip-warn`, `.vpill-warn`, `bar-warning` | theme.py |
| `error` / `@theme_error` | `#CB7C94` | Down / failed: `.tier-crit`, `bar-error` | theme.py |
| `selection` / `@theme_selection` | `#232A40` | Hover of rows and pills, selected sidebar row | theme.py |
| `accent_fg_color` | `= background` (`#17191e`) | Text on accent fills (`suggested-action`) | theme.py `colors_css` |

Chart series (`Palette.series`, read by Cairo widgets through `chart_series(i)`):

| Index | Value | Used in the artboards for |
|---|---|---|
| 0 | `#8FB8DD` | Pi group series (`historial.py`) |
| 1 | `#B99BF2` | Abito (purple) |
| 2 | `#E0C15A` | Gufo (same as accent) |
| 3 | `#A4DAA7` | — |
| 4 | `#C99AD6` | — |
| 5 | `#7FB4CA` | — |
| 6 | `#A3B5D6` | — |
| 7 | `#8394A3` | Secondary text (proposal, see contrast note) |

### Contrast (measured, WCAG relative luminance)

| Pair | Ratio | AA text (4.5:1) |
|---|---|---|
| `text` on `background` / `element` / `selection` | 16.2 / 14.9 / 13.1 | pass |
| `accent` on `element` | 9.2 | pass |
| `background` on `accent` (button label) | 10.0 | pass |
| `green` / `warning` / `error` on `element` | 9.2 / 8.8 / 5.3 | pass |
| `error` on `subtle` | 4.9 | pass |
| `series[1]` `#B99BF2` / `blue` on `element` | 6.9 / 7.1 | pass |
| `series[7]` `#8394A3` on `background` / `element` / `subtle` / `selection` | 5.6 / 5.2 / 4.8 / 4.6 | pass |
| `accent` fill on `border` trough (progress) | 7.1 | pass (≥ 3:1 non-text) |
| `border` on `element` (trough, hairlines) | 1.3 | decorative only |
| **`muted` `#5C6170` on `background` / `element`** | **2.85 / 2.6** | **fail** (also below 3:1 for large text) |

Flag: `muted` is the source token for captions and secondary labels, but it fails AA for text on the
graphite surfaces. The artboards from 08 on use `muted` only for non-text marks (idle lamp outlines,
pending progress segments, borders of disabled controls) and use `series[7]` `#8394A3` for readable
secondary text. That reuse is a **proposal awaiting the user's decision**, not a source rule; the real
app still paints `.muted` text with `#5C6170`.

## Typography (`BASE_CSS`)

- UI family: not declared in the source → libadwaita default (Adwaita Sans / Cantarell). Artboards use
  the system stack `"Adwaita Sans", Cantarell, system-ui, sans-serif`.
- Mono family (`.value`, `.num`, `.kpi-value`, `.kbd`, `.age-pill`, `.sidebar-badge`):
  `"JetBrains Mono", "Fira Code", "Cascadia Code", Consolas, monospace` (badges/kbd/age omit Cascadia).
- `.mono` is declared twice in `BASE_CSS`; the later rule wins: generic `monospace`, 12 px.

| Role | Size / weight | Extra | Source class |
|---|---|---|---|
| KPI value | 22 px / 700 mono | — | `.kpi-value` |
| Row name | 13 px / 600 | — | `.row-name` |
| Update pill | 13 px / 700 | coloured by state | `.vpill` |
| Mono value | 12 px mono | — | `.mono` |
| Sidebar label | 12 px | — | `.sidebar-label` |
| Card title | 11 px / 700 | letter-spacing .08em, uppercase in markup | `.card-title` |
| Section head | 11 px / 700 | letter-spacing .1em | `.section-head` |
| Muted / chip | 11 px | — | `.muted`, `.chip` |
| KPI label | 10 px / 600 | letter-spacing .06em | `.kpi-label` |
| Badge / kbd / age | 10 px mono (kbd 700) | — | `.sidebar-badge`, `.kbd`, `.age-pill` |

## Spacing (`BASE_CSS` paddings)

`1px 5px` (kbd) · `1px 8px` (age pill) · `2px 10px` (chip) · `5px 10px` (switch pill) ·
`8px 10px` (card) · `8px 12px` (kpi). Sidebar rows: `min-height: 22px`, padding 0.

## Radius

`4px` (kbd) · `6px` (switch pill, legend/row hover, scrollbar slider) · `8px` (card, kpi) ·
`999px` (chip, age pill).

## Shadows

None in the source: `headerbar { box-shadow: none; }`. The only inset shadow is the selected sidebar
row marker `box-shadow: inset 3px 0 0 @theme_accent`. Dialog and toast elevation come from
libadwaita itself, not from project tokens.

## Component rules (`BASE_CSS`)

- **Card** `.card`: `element` surface, 1 px `border`, radius 8, padding 8×10. Title `.card-title`.
- **Progress bar** `progressbar`: fill = accent by default; `.bar-blue/-green/-warning/-error` per kind;
  `.inv-ctx` blue for context bars.
- **Buttons**: `suggested-action` = accent fill with `background` text. Disabled `suggested` /
  `destructive` = `subtle` fill, `muted` text, 1 px `border`, opacity 1 (must not read as active).
- **Switch pill** `.switch-pill`: `element`, 1 px `border`, radius 6, padding 5×10; hover `selection`;
  disabled opacity .55; contains `.kbd`.
- **Kbd** `.kbd`: mono 10/700, `subtle` fill, `border`, radius 4, `muted` text.
- **Chip** `.chip`: `subtle`, `border`, radius 999, padding 2×10, 11 px; `.chip-dim` muted text;
  `.chip-warn` warning border + text.
- **Age pill** `.age-pill`: mono 10, radius 999, `subtle` + `border`; `.age-ok` green text,
  `.age-warn` warning text and border.
- **Update pill** `.vpill`: 13/700; `.vpill-ok` green, `.vpill-warn` warning, `.vpill-acc` accent.
- **Sidebar** `.navigation-sidebar`: rows min-height 22; selected = `selection` + inset 3 px accent bar,
  label in accent; badges mono 10 muted (text on selected).
- **Rows** `.legend-row`, `.row-button`: radius 6, hover `selection`.
- **State colours** `.tier-ok/-warn/-crit`: green / warning / error — always paired with a shape or
  word in the artboards (lamp circle / diamond / square).
- **Separator / scrollbar**: `border` colour; slider radius 6.

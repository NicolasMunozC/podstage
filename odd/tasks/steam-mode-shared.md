# steam-mode-shared

Per-sandbox choice between an isolated Steam install and reusing the desktop Steam
install, decided at sandbox creation and immutable afterwards. Branch:
`feat/steam-mode-shared` (from `b89433d`).

## Specs

**S1 — Shared Steam is the fix for non-Steam games.** A sandbox created in shared
mode runs the host's real Steam installation, so non-Steam shortcuts, grid art,
`localconfig.vdf`, `compatibilitytools.d` and `compatdata/<appid>` are the same
files as on the desktop. No synchronization step, no manual symlinks, no
per-appid prefix linking.

> "como lo podria hacer para compartirlos igual y asi tener como la misma sesion
> compartida del steam que ejecuto normal y del que ejecuto dentro de podstage
> modo consola? Es para que se guarden las mismas partidas y los mismos juegos que
> no son de steam, las mismas configuraciones y todo"

**S2 — One Steam at a time, always.** The single-instance guard applies in every
mode and is never relaxed by the new option. In shared mode it is more critical
than in isolated mode, because both clients write the same files. The check runs
before any mount or `podman run`, and a shared sandbox also refuses to start while
another shared sandbox runs.

> "el bloqueo de no abrir mas de 1 steam a la vez debe existir siempre. Para que
> no genere problemas con steam y sus servicios."

**S3 — Isolation exists for multiple accounts, not for the same one.** Isolated
mode = its own Steam install and its own login (multi-account). Shared mode = the
same account, one install, two front-ends (desktop / podstage console). What
podstage still isolates in shared mode is the session: own compositor, own display,
desktop untouched.

> "la idea de islar el steam es para usar multiples cuentas, pero si voy a usar la
> misma no es mejor que se comparta y que sea el mismo steam con posibilidad de
> ejecutarse de dos formas"

**S4 — Default is isolated.** The schema default stays `isolated`, preserving
current behaviour for existing configurations.

> "El default es aislado porque el proposito del proyecto"

**S5 — The mode is chosen only at creation.** The sandbox creation dialog exposes
the choice; it is not editable afterwards. The UI disables it when editing an
existing sandbox and states that the sandbox must be recreated to change it.

> "la opcion debe ser una configuracion de inicio, y no una configuracion
> editable. Osea solo se selecciona desde un inicio en la creacion del sandbox
> luego ya no se puede modificar"

**S6 — Immutability rationale is enforced, not just documented.** Isolated→shared
would orphan the sandbox's own install, login and prefixes. Shared→isolated would
need to bootstrap and log in from scratch, and switching must never write to the
real Steam installation.

> "si es aislado tiene sus propias cosas, y viceversa, si es tu compartido no
> puedes pasar a un aislado porque tocaria tu instalacion principal de steam."

**S7 — Shared mode must refuse to start when the host root does not resolve.**
Verified against the image's `/usr/lib/steam/bin_steam.sh`
(`steam 1.0.0.87-3`, `STEAMSCRIPT_VERSION=1.0.0.87`): `check_bootstrap` only tests
`[[ -n "$data" && -x "$data/steam.sh" ]]` and never compares versions, so the
packaged version can neither update nor downgrade the host install. But when that
test fails, `install_bootstrap` runs `mkdir -p` plus `tar xJf
bootstraplinux_ubuntu12_32.tar.xz` into `$DEFAULTSTEAMDIR` (`~/.local/share/Steam`),
which in shared mode is the mounted host root. A broken symlink or a missing mount
would therefore extract the bootstrap over the real installation. The guard is
mandatory: resolve the link and verify `steam.sh` is executable before starting,
and abort with an explicit error otherwise.

**S8 — Shared mode writes nothing to the host that it does not have to.** Libraries
mount plain read-write with no overlay (an overlay is meaningless when the install
is shared); the provisioner does not run, because `purge_orphan_downloads` would
delete the host's in-flight downloads and `mirror_compat_mappings` would rewrite
the host's `config.vdf`. The provisioner is not "skipped for speed": running it in
shared mode is a data-loss path.

**S9 — The boundary is what Steam owns, not `$HOME`.** Shared mode shares
everything Steam keeps: `userdata` (shortcuts, grid art, localconfig), the
libraries (`libraryfolders.vdf`, `appmanifest_*.acf`), `compatibilitytools.d` and
`compatdata` (Proton prefixes). It does **not** share `$HOME`: inside the
container that is still the sandbox (`/home/player`). A game that saves inside
its Proton prefix is covered, because that prefix is the shared one. A native
build that writes to `~/.config`, `~/.local/share` or `~/Documents` without
Proton keeps separate saves, and the honest fix there is an `extra_mounts` entry
for that directory or forcing Proton for that game.

**S10 — Full parity: the real `$HOME` (opt-in, shared sandboxes only).** The
user's goal is not "shared Steam" but *the same computer*: install an emulator on
the desktop, get into bed, start the shared console and continue. So an optional
mode mounts the host HOME **at its real absolute path** read-write and runs the
container with `HOME` set to it. Every `~/.config`, `~/.local/share`,
`~/Documents` and every absolute path inside a game's or emulator's configuration
then resolves exactly as on the desktop, with nothing derived, parsed or guessed.

Consequences that are part of the spec, not surprises:

- The stream writes to the real home. A configuration broken in the stream is
  broken on the desktop. That is the accepted trade, and it is the only way to
  get real parity without guesswork.
- The Steam root arrives inside the home mount, so the separate root mount and the
  `.steam` link are not used in this mode; the launcher is verified instead (S7
  still applies).
- Podstage's own state must stay out of the real home: the sandbox's
  `.cache/podstage` is mounted over `<HOME>/.cache/podstage` inside the container,
  so the host-side readers keep working unchanged and the real home gains no
  podstage files.
- The container's Steam is then indistinguishable from the desktop Steam **by
  path**, which is exactly the point. The single-instance guard can therefore no
  longer tell them apart by HOME path (S2 still binds): it must identify
  podman-owned processes instead.
- `share_home` is creation-time only, like `steam_mode`, and is ignored unless the
  sandbox is shared.
- Removable media lands in `/run/media`, `/media` or `/mnt` depending on who
  mounted it, and all three are mounted where they exist. A game on an external
  disk therefore launches without a per-folder entry, and anything inside the home
  needs nothing at all.

## Tasks

Slice baseline: `b89433d`, already merged into `fork/main`. The 792 lines carried
from `fix/sandbox-login-ufw-gamepad` belong to that feature, not to this one, so
this feature's slice counter starts at commit 1 below (not at `origin/main`).

- T1 — Add `steam_mode: str = "isolated"` to `SessionConfig` with generic
  serialization round-trip; rejected values fall back to isolated. `S4,S5`.
  inline. commit 321c894.
- T2 — Thread the field `session.py:_options` → `RuntimeOptions` → `podman_run_args`.
  `S1`. inline. commit 321c894.
- T3 — Shared mount branch in `container_flags`: host Steam root rw at its real
  absolute path, non-default libraries rw with no overlay, host root excluded from
  `library_paths` to avoid a nested double mount. `S1,S8`. inline. commit 321c894.
- T4 — Skip provisioning and overlays in shared mode; do not call
  `provisioner.ensure_all`, `ensure_overlay_dirs`, or `drop_foreign_steam_links`.
  `S8`. inline. commit 321c894 (provisioning); `drop_foreign_steam_links` is
  avoided by refusing `setup()` in shared mode.
- T5 — Create `<home>/.steam/steam` → host root before start; never mount the host
  `~/.steam`, so `bin_steam.sh` cannot forward the command line to a running desktop
  Steam via `steam.pipe`. `S1,S2,S7`. inline. commit 321c894.
  Amendment: a sandbox previously bootstrapped isolated keeps `.steam/root` and the
  `bin`/`bin32`/`bin64`/`sdk32`/`sdk64` links pointing at its own client, plus a dead
  `steam.pid` and a `steam.pipe` with no reader — and the launcher follows those
  before it reaches the host install. The guard now clears that state and points
  both `steam` and `root` at the host root.
- T6 — Pre-start guard: resolve the link, require an executable `steam.sh`, abort
  with a clear message otherwise. `S7`. inline. commit 321c894.
  Pending: the `doctor` readiness check, deferred with the UI work.
- T7 — Single-instance invariant enforced for both modes with shared-mode
  detection that does not rely on the HOME path (both resolve to the same HOME);
  refuse a second shared sandbox. `S2`. inline. commit 3.
- T8 — Teardown never touches the shared root: `clear_overlays` and sandbox
  deletion are no-ops for shared state, and the path guard must not permit removing
  the real installation. `S6`. inline. commit 3.
- T9 — GUI: mode selector in the creation dialog, disabled when editing with an
  explanatory note; wire the field into the explicit `SessionConfig(...)`
  reconstruction in `_on_accept` so edits cannot silently reset it. Add i18n
  catalog entries. `S5`. inline. commit 4.
- T10 — Tests: config round-trip and invalid value; shared mount flags; provisioner
  never called in shared mode; guard aborts on a broken link; teardown leaves the
  shared root intact; GUI field survives an edit. `S1-S8`. inline. commit 4.
- T11 — Docs: README/CHANGELOG entry explaining both modes and the exclusivity
  rule. `S1,S2,S3`. inline. commit 4.
- T12 — GUI gates must know the mode. The Sandboxes page has its own host-side
  bootstrap (`sandbox_page.py:_on_bootstrap`, which launches `steam` with
  `sandbox.steam_env` and calls `drop_foreign_steam_links`) and the Session page
  has its own login gate (`session_page.py:_on_start`). Neither went through
  `Session.setup()`, so both had to learn `steam_mode`. `S1,S5,S7`. inline.
- T13 — Documents the S9 boundary: shared mode covers what Steam owns, not
  `$HOME`. README/CHANGELOG says so next to the existing `extra_mounts` advice, so
  "my save did not carry over" has an answer before it becomes a bug report.
  `S9`. inline.
- T14 — `share_home: bool = False` on `SessionConfig`, normalized to False unless
  `steam_mode == "shared"`; creation-time only. `S10`. inline. commit 05b51e6.
- T15 — Runtime: mount the real home at its real path rw, set `HOME` in the
  container env, shadow `<sandbox>/.cache/podstage` over `<HOME>/.cache/podstage`,
  skip the separate Steam-root mount and the `.steam` link, and verify the real
  launcher instead. `S7,S10`. inline. commit 05b51e6.
- T16 — Single-instance guard: cgroup ownership added on top of the image-HOME and
  sandbox-HOME path exclusions, so a second session is still refused in both modes
  and the container's own Steam never counts as the desktop's. `S2,S10`. inline.
  commit 05b51e6.
- T17 — GUI: creation-only checkbox next to the Steam mode, enabled only for a
  shared sandbox and disabled when editing, carried through the explicit rebuild,
  with i18n. `S10`. inline. commit 05b51e6.
- T18 — Tests: config default and normalization, round-trip, the home mount and
  state shadow and `HOME`, the root mount being skipped, the launcher guard, and
  the ownership-based detection. `S2,S7,S10`. inline. commit 05b51e6.
- T19 — Docs: README/CHANGELOG for the three levels (isolated, shared, shared +
  full parity) and their blast radius. `S9,S10`. inline.

## Log

- L1 (verbatim, user): "Sabes que a veces tengo juegos del "caribe", que los agrego
  a steam como juegos que no son de steam para ejecutarlos en modo consola, pero
  como tengo esto de podstage los juegos que se comparten son los que estan en la
  biblioteca de steam, pero no los que no son de steam agregados manualmente, como
  lo podria hacer para compartirlos igual y asi tener como la misma sesion
  compartida del steam que ejecuto normal y del que ejecuto dentro de podstage modo
  consola? Es para que se guarden las mismas partidas y los mismos juegos que no
  son de steam, las mismas configuraciones y todo"
- L2 (user): the single-instance block must always exist, to avoid problems with
  Steam and its services. Isolation is for multiple accounts; for the same account
  it is better to share one Steam with two launch modes (desktop / console), never
  both at the same time.
- L3 (user): default isolated, because that is the project's purpose; the choice
  belongs in sandbox creation as a start-only setting and must not be editable
  later, for exactly the orphaned-state reasons above. The branch must come from our
  latest work, because that branch carries the changes required for podstage to
  work on this system. Merging to our own fork's main does not require the upstream
  developer's approval.
- L4 (evidence, spike): recorded in S7. `/usr/bin/steam` execs
  `/usr/lib/steam/steam` → `/usr/lib/steam/bin_steam.sh`; version-comparison risk
  eliminated; host-root extraction risk identified and promoted to S7.
- L5 (field failure, user): a sandbox created in shared mode asked for a Steam
  login. Cause: the Sandboxes page ran its own host-side bootstrap, which
  `drop_foreign_steam_links` used to delete `.steam/steam` (the shared link) and
  then launched `steam` with `HOME=<sandbox>`, bootstrapping 2.5 GB of an isolated
  client that asks to sign in. The Session page's own login gate sent the user
  there first, because it checked `steam_logged_in` for a sandbox that has no
  login of its own. Both GUI gates now skip the isolated questions in shared
  mode; `runtime.ensure_shared_steam_link` repairs a link left pointing at the
  sandbox's own client on the next start.
- L6 (field failure, user): the shared session started, mounted correctly (the link
  was repaired at start, no provisioning ran, and an in-container probe resolved
  `.steam/steam` → the host root with an executable `steam.sh`), then the container
  died 69 s in: labwc's child exited with 6 after 3 s and Steam never wrote a
  `bootstrap_log` entry. Not networking: the Sunshine web UI had been reachable
  while the container was briefly up, and nothing was listening afterwards, which is
  why moonlight listed no host. The sandbox's `.steam` was still the one left by the
  earlier host-side bootstrap (root/bin/sdk links to its own client, dead steam.pid,
  readerless steam.pipe). Clearing it is the fix; a clean recreate confirms it.
- L7 (user): asked for non-Steam games and their save folders to be mounted
automatically. Decision: game folders are derivable from `shortcuts.vdf`; save
folders are not, because a game's save directory name is not its shortcut name and
a wrong guess mounts the real `~/.config`. The user chose **full home parity**
instead (S10), which makes both questions moot for anything inside the home and
matches the actual goal: the same computer, run from the bed.
- L8 (field result, user): a game added on the desktop appeared on the phone when
the shared sandbox started — the feature's end-to-end goal met. It did not launch
until the profile's `extra_mounts` got its folder, which prompted the question
whether that step is avoidable. It is: the folder was `~/Descargas`, already
covered by parity (same inode inside the container and on the host), so that entry
was redundant — the failed attempt predated the parity sandbox. The removable
media roots were then mounted as well, so an external disk no longer needs a
per-folder entry either. Trade recorded: the session can now write to removable
media too, exactly as the desktop can.

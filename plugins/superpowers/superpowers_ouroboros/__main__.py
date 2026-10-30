from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .handoff_builder import prepare_handoff
from .skill_index import discover_skills, get_skill, write_index


def _json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _default_output_root() -> Path:
    """Return a runtime artifact root outside immutable plugin code.

    Installed plugins are trust subjects: the Ouroboros firewall hashes the
    plugin home and refuses later invocations if those bytes drift. When the
    dispatcher runs this module from an installed plugin home, writing the
    default ``./.omx`` there mutates trusted code and breaks repeat dispatch.

    Prefer an explicit dispatcher-provided output root, then a dispatcher/user
    workdir, then the current workspace. If the current working directory is
    the plugin source/install tree, fall back to a user-state artifact root so
    runtime evidence never changes installed plugin bytes.
    """
    env_output = os.environ.get("OUROBOROS_PLUGIN_OUTPUT_DIR")
    if env_output:
        return Path(env_output).expanduser()

    env_workdir = os.environ.get("OUROBOROS_PLUGIN_WORKDIR")
    if env_workdir:
        return Path(env_workdir).expanduser() / ".omx" / "superpowers"

    cwd = Path.cwd()
    from .skill_index import PLUGIN_ROOT

    if _is_relative_to(cwd, PLUGIN_ROOT):
        return Path.home() / ".ouroboros" / "plugin-artifacts" / "superpowers"
    return cwd / ".omx" / "superpowers"


def _output_root(value: str | None) -> Path:
    root = Path(value).expanduser() if value else _default_output_root()
    return root.resolve()


def cmd_list(args: argparse.Namespace) -> int:
    root = _output_root(args.output_dir)
    index_path = write_index(root / "skill-index.json")
    _json({"status": "ok", "skill_count": len(discover_skills()), "skill_index_path": str(index_path), "skills": [s.to_json() for s in discover_skills()]})
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    try:
        skill = get_skill(args.skill)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _json({"status": "ok", "skill": skill.to_json()})
    return 0


def _prepare(args: argparse.Namespace, skill_name: str) -> int:
    try:
        skill = get_skill(skill_name)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    root = _output_root(args.output_dir)
    write_index(root / "skill-index.json")
    payload = prepare_handoff(
        skill,
        goal=args.goal or "",
        user_input=args.input or "",
        output_root=root,
        invoked_by=args.invoked_by,
    )
    _json(payload)
    return 0


def cmd_prepare_handoff(args: argparse.Namespace) -> int:
    return _prepare(args, args.skill)


def _output_parent_parser() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--output-dir",
        default=argparse.SUPPRESS,
        help=(
            "Artifact root. May appear before or after the subcommand; "
            "defaults to OUROBOROS_PLUGIN_OUTPUT_DIR, "
            "OUROBOROS_PLUGIN_WORKDIR/.omx/superpowers, or a safe user/workspace root."
        ),
    )
    return parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m superpowers_ouroboros", parents=[_output_parent_parser()])
    parser.set_defaults(output_dir=None)
    sub = parser.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser("list", parents=[_output_parent_parser()], help="List pinned upstream Superpowers skills and command mappings")
    list_p.set_defaults(func=cmd_list)

    inspect_p = sub.add_parser("inspect", parents=[_output_parent_parser()], help="Inspect one Superpowers skill mapping")
    inspect_p.add_argument("skill")
    inspect_p.set_defaults(func=cmd_inspect)

    prep_p = sub.add_parser("prepare-handoff", parents=[_output_parent_parser()], help="Prepare an Ouroboros-native handoff for a skill")
    prep_p.add_argument("skill")
    prep_p.add_argument("--goal", default="")
    prep_p.add_argument("--input", default="")
    prep_p.add_argument("--invoked-by", default="direct", choices=["direct", "handoff", "auto", "team"])
    prep_p.set_defaults(func=cmd_prepare_handoff)

    run_p = sub.add_parser("run", parents=[_output_parent_parser()], help="v0 safe run alias: prepare handoff without executing destructive actions")
    run_p.add_argument("skill")
    run_p.add_argument("--goal", default="")
    run_p.add_argument("--input", default="")
    run_p.add_argument("--invoked-by", default="direct", choices=["direct", "handoff", "auto", "team"])
    run_p.set_defaults(func=cmd_prepare_handoff)

    for skill in discover_skills():
        skill_p = sub.add_parser(skill.name, parents=[_output_parent_parser()], help=skill.description[:120] or f"Prepare {skill.name} handoff")
        skill_p.add_argument("--goal", default="")
        skill_p.add_argument("--input", default="")
        skill_p.add_argument("--invoked-by", default="direct", choices=["direct", "handoff", "auto", "team"])
        skill_p.set_defaults(func=lambda args, name=skill.name: _prepare(args, name))

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

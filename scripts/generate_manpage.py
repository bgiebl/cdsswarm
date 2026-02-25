#!/usr/bin/env python3
"""Generate man/cdsswarm.1 from the CLI argparse parsers.

Requires argparse-manpage: pip install argparse-manpage

Usage:
    ./venv/bin/python scripts/generate_manpage.py
"""

from __future__ import annotations

import os
import sys
import textwrap

# Ensure the source tree is importable.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir, "src"))

from argparse_manpage.manpage import Manpage

from cdsswarm import __version__
from cdsswarm.cli import (
    _build_cancel_parser,
    _build_generate_parser,
    _build_parser,
)

OUT = os.path.join(os.path.dirname(__file__), os.pardir, "man", "cdsswarm.1")


def _format_parser_options(parser) -> str:
    """Render a parser's optional and positional arguments as roff."""
    lines: list[str] = []
    for action in parser._actions:
        if action.option_strings:
            opts = ", ".join(
                f"\\fB{o}\\fR" for o in action.option_strings
            )
            if action.metavar:
                opts += f" \\fI{action.metavar}\\fR"
            elif action.type and action.type is not bool:
                opts += f" \\fI{action.dest.upper()}\\fR"
        else:
            if isinstance(action, type(parser._subparsers)):
                continue
            opts = f"\\fI{action.dest}\\fR"
        lines.append(f".TP\n{opts}")
        if action.help:
            help_text = action.help.replace("-", "\\-")
            lines.append(help_text)
    return "\n".join(lines)


def main() -> None:
    parser = _build_parser()
    parser.epilog = None  # Avoid duplicate COMMENTS section; we add SUBCOMMANDS.

    man = Manpage(parser)
    man.source = f"cdsswarm {__version__}"
    man.manual = "cdsswarm Manual"

    # -- SUBCOMMANDS section --------------------------------------------------
    gen_parser = _build_generate_parser()
    cancel_parser = _build_cancel_parser()

    subcommands = textwrap.dedent("""\
        The following subcommands are available.
        Run \\fBcdsswarm \\fISUBCOMMAND\\fB \\-\\-help\\fR for full usage.
        .SS cdsswarm generate
        {gen_desc}
        .P
        Synopsis: \\fBcdsswarm generate\\fR [\\-\\-split\\-by \\fIFIELDS\\fR] \
[\\-o \\fIFILE\\fR] [\\-\\-dry\\-run] \\fItemplate_file\\fR
        {gen_opts}
        .SS cdsswarm cancel
        {cancel_desc}
        .P
        Synopsis: \\fBcdsswarm cancel\\fR [\\-y] [\\fIrequest_id\\fR ...]
        {cancel_opts}""").format(
        gen_desc=gen_parser.description,
        gen_opts=_format_parser_options(gen_parser),
        cancel_desc=cancel_parser.description,
        cancel_opts=_format_parser_options(cancel_parser),
    )
    man.add_section("subcommands", "=", subcommands)

    # -- CONFIGURATION --------------------------------------------------------
    config_text = textwrap.dedent("""\
        Settings are resolved in order: CLI flags > config file > defaults.
        The config file is \\fB.cdsswarm.toml\\fR in the current directory or \
any parent.
        See the project README for all available keys.""")
    man.add_section("configuration", "=", config_text)

    # -- EXIT STATUS ----------------------------------------------------------
    exit_text = textwrap.dedent("""\
        .TP
        \\fB0\\fR
        All downloads completed successfully.
        .TP
        \\fB1\\fR
        One or more downloads failed, or the run was cancelled.""")
    man.add_section("exit status", "=", exit_text)

    # -- AUTHOR ---------------------------------------------------------------
    man.add_section("author", "=", "Benedikt Giebl <b.giebl@protonmail.com>")

    # -- SEE ALSO -------------------------------------------------------------
    man.add_section(
        "see also",
        "=",
        "Project homepage: \\fIhttps://github.com/bgiebl/cdsswarm\\fR",
    )

    # -- BUGS -----------------------------------------------------------------
    man.add_section(
        "bugs",
        "=",
        "Report bugs at \\fIhttps://github.com/bgiebl/cdsswarm/issues\\fR",
    )

    out_path = os.path.normpath(OUT)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        f.write(str(man))

    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()

"""Common tasks to build, check and publish Spyder-Docs."""

# Standard library imports
import contextlib
import logging
import os
import re
import tempfile
import shutil
import sys
import webbrowser
from pathlib import Path

# Third party imports
import nox  # pylint: disable=import-error
import nox.logger  # pylint: disable=import-error


# --- Global constants --- #

nox.options.error_on_external_run = True
nox.options.sessions = ["build"]
nox.options.default_venv_backend = "none"

ORG_NAME = "spyder-ide"
REPO_NAME = "spyder-docs"
REPO_URL_HTTPS = "https://github.com/{user}/{repo}.git"
REPO_URL_SSH = "git@github.com:{user}/{repo}.git"

IGNORE_REVS_FILE = ".git-blame-ignore-revs"

CANARY_COMMAND = ("sphinx-build", "--version")
BUILD_INVOCATION = ("python", "-m", "sphinx")
BUILD_OPTIONS = ("-n", "-W", "--keep-going")

SOURCE_DIR = Path("doc").resolve()
BUILD_DIR = Path("doc/_build").resolve()
STATIC_DIR_NAME = Path("_static")
SOURCE_STATIC_DIR = SOURCE_DIR / STATIC_DIR_NAME

HTML_BUILDER = "html"
HTML_BUILD_DIR = BUILD_DIR / HTML_BUILDER
HTML_INDEX_PATH = HTML_BUILD_DIR / "index.html"

SOURCE_LANGUAGE = "en"
TRANSLATION_LANGUAGES = ("es",)
ALL_LANGUAGES = (SOURCE_LANGUAGE,) + TRANSLATION_LANGUAGES
LANGUAGE_SWITCHER_SOURCE = SOURCE_STATIC_DIR / "js" / "language_switcher.js"

LOCALE_DIR = SOURCE_DIR / "locales"
GETTEXT_BUILDER = "gettext"
GETTEXT_BUILD_DIR = BUILD_DIR / GETTEXT_BUILDER
POT_DIR = LOCALE_DIR / "pot"
PO_LINE_WIDTH = 0

LATEST_VERSION = 5
BASE_URL = "https://docs.spyder-ide.org"

CONF_PY = SOURCE_DIR / "conf.py"
SCRIPT_DIR = Path("scripts").resolve()

CI = "CI" in os.environ


# ---- Helpers ---- #


@contextlib.contextmanager
def set_log_level(logger=nox.logger.logger, level=logging.CRITICAL):
    """Context manager to set a logger log level and reset it after."""
    prev_level = logger.level
    logger.setLevel(level)
    try:
        yield
    finally:
        logger.setLevel(prev_level)


def modify_asset(pattern, replacement, asset_path):
    """Perform a regex find and replace in an asset."""
    source_content = Path(asset_path).read_text(encoding="UTF-8")
    modified_content = re.sub(pattern, replacement, source_content)
    Path(asset_path).write_text(modified_content, encoding="UTF-8")


def split_sequence(seq, *, sep="--"):
    """Split a sequence by a single separator."""
    if sep not in seq:
        seq.append(sep)
    idx = seq.index(sep)
    return seq[:idx], seq[idx + 1 :]


def process_filenames(filenames, source_dir=SOURCE_DIR):
    """If filepaths are missing the source directory, add it automatically."""
    source_dir = Path(source_dir)
    filenames = [
        (
            str(source_dir / filename)
            if source_dir not in Path(filename).resolve().parents
            else filename
        )
        for filename in filenames
    ]
    return filenames


def extract_option_values(options, option_names, *, split_csv=False):
    """Extract particular option values from a sequence of options."""
    option_values = []
    remaining_options = []
    if isinstance(option_names, str):
        option_names = [option_names]

    save_next_option = False
    for option in options:
        if save_next_option:
            if split_csv:
                option_values += list(option.strip(",").split(","))
            else:
                option_values.append(option)
            save_next_option = False
        elif option in option_names:
            save_next_option = True
        else:
            remaining_options.append(option)

    return option_values, remaining_options


def construct_sphinx_invocation(
    posargs=(),
    *,
    builder=HTML_BUILDER,
    source_dir=SOURCE_DIR,
    build_dir=None,
    build_options=BUILD_OPTIONS,
    extra_options=(),
    build_invocation=BUILD_INVOCATION,
):
    """Reusably build a Sphinx invocation string from the given arguments."""
    cli_options, filenames = split_sequence(list(posargs))
    filenames = process_filenames(filenames, source_dir=source_dir)
    builders, cli_options = extract_option_values(
        cli_options, ["--builder", "-b"], split_csv=False
    )
    builder = builders[-1] if builders else builder
    build_dir = BUILD_DIR / builder if build_dir is None else build_dir

    if CI:
        build_options = list(build_options) + ["--color"]

    sphinx_invocation = [
        *build_invocation,
        "-b",
        builder,
        *build_options,
        *extra_options,
        *cli_options,
        "--",
        str(source_dir),
        str(build_dir),
        *filenames,
    ]
    return sphinx_invocation


# ---- Dispatch ---- #


# Workaround for Nox not (yet) supporting shared venvs
# See: https://github.com/wntrblm/nox/issues/167
@nox.session(venv_backend="virtualenv", reuse_venv=True)
def _execute(session):
    """Dispatch tasks to run in a common environment. Do not run directly."""
    if not session.posargs or isinstance(session.posargs[0], str):
        raise ValueError(
            "Must pass a list of functions to execute as first posarg"
        )

    if not session.posargs or session.posargs[0] is not _install:
        # pylint: disable=too-many-try-statements
        try:
            with set_log_level():
                session.run(
                    *CANARY_COMMAND, include_outer_env=False, silent=True
                )
        except nox.command.CommandFailed:
            print("Installing dependencies in isolated environment...")
            _install(session, use_posargs=False)

    if session.posargs:
        for task in session.posargs[0]:
            task(session)


# ---- Install ---- #


def _install(session, *, use_posargs=True):
    """Execute the dependency installation."""
    posargs = session.posargs[1:] if use_posargs else ()
    session.install("-r", "requirements.txt", *posargs)


@nox.session
def install(session):
    """Install the project's dependencies (passes through args to pip)."""
    session.notify("_execute", posargs=([_install], *session.posargs))


# ---- Utility ---- #


def _build_help(session):
    """Print Sphinx --help."""
    session.run(*BUILD_INVOCATION, "--help")


@nox.session(name="help")
def build_help(session):
    """Get help with the project build."""
    session.notify("_execute", posargs=([_build_help],))


def _run(session):
    """Run an arbitrary command invocation in the project's venv."""
    posargs = session.posargs[1:]
    if not posargs:
        session.error("Must pass a command invocation to run")
    session.run(*posargs)


@nox.session()
def run(session):
    """Run any command."""
    session.notify("_execute", posargs=([_run], *session.posargs))


def _clean(session):
    """Remove the build directory."""
    print(f"Removing build directory {BUILD_DIR.as_posix()!r}")
    ignore_flag = "--ignore"
    should_ignore = ignore_flag in session.posargs

    try:
        shutil.rmtree(BUILD_DIR, ignore_errors=should_ignore)
    except FileNotFoundError:
        pass
    except Exception:
        print(f"\nError removing files; pass {ignore_flag!r} flag to ignore\n")
        raise


@nox.session
def clean(session):
    """Clean build artifacts (pass -i/--ignore to ignore errors)."""
    _clean(session)


# --- Set up --- #


def _setup_remotes(session):
    """Set up the origin and upstream remote repositories."""
    remote_cmd = ["git", "remote"]
    posargs = list(session.posargs)
    https = "--https" in posargs
    ssh = "--ssh" in posargs

    if posargs and not isinstance(posargs[0], str):
        posargs = posargs[1:]
    username_args = extract_option_values(posargs, "--username")[0]
    if https == ssh:
        session.error("Exactly one of '--https' or '--ssh' must be passed")

    # Get current origin details
    origin_url_cmd = (*remote_cmd, "get-url", "origin")
    origin_url = session.run(
        *origin_url_cmd, external=True, silent=True, log=False
    ).strip()
    if "https://" not in origin_url:
        origin_url = origin_url.split(":")[-1]
    origin_user, origin_repo = origin_url.split("/")[-2:]
    if origin_repo.endswith(".git"):
        origin_repo = origin_repo[:-4]

    # Check username
    if username_args:
        origin_user = username_args[0].strip().lstrip("@")
    elif origin_user.lower() == ORG_NAME.lower():
        code_host = REPO_URL_HTTPS.split(":")[1].lstrip("/").split("/")[0]
        session.warn(
            "Origin remote currently set to upstream; should be your fork.\n"
            f"To fix, fork it and pass --username <Your {code_host} username>"
        )

    # Set up remotes
    existing_remotes = (
        session.run(*remote_cmd, external=True, silent=True, log=False)
        .strip()
        .split("\n")
    )
    for remote, user_name, repo_name in (
        ("origin", origin_user, origin_repo),
        ("upstream", ORG_NAME, REPO_NAME),
    ):
        action = "set-url" if remote in existing_remotes else "add"
        fetch_url = REPO_URL_HTTPS.format(user=user_name, repo=repo_name)
        session.run(*remote_cmd, action, remote, fetch_url, external=True)

        ssh_url = REPO_URL_SSH.format(user=user_name, repo=repo_name)
        push_url = ssh_url if ssh else fetch_url
        session.run(
            *remote_cmd, "set-url", "--push", remote, push_url, external=True
        )

    session.run("git", "fetch", "--all", external=True)


@nox.session(name="setup-remotes")
def setup_remotes(session):
    """Set up the Git remotes; pass --https or --ssh to specify URL type."""
    _setup_remotes(session)


def _ignore_revs(session):
    """Configure the Git ignore revs file to the repo default."""
    if not IGNORE_REVS_FILE:
        return
    session.run(
        "git",
        "config",
        "blame.ignoreRevsFile",
        IGNORE_REVS_FILE,
        external=True,
    )


@nox.session(name="ignore-revs")
def ignore_revs(session):
    """Configure Git to ignore noisy revisions."""
    _ignore_revs(session)


@nox.session()
def setup(session):
    """Set up the project; pass --https or --ssh to specify Git URL type."""
    session.notify(
        "_execute",
        posargs=(
            [
                _ignore_revs,
                _setup_remotes,
                _install_hooks,
                _clean,
            ],
            *session.posargs,
        ),
    )


# ---- Build ---- #


def _build(session):
    """Execute the docs build."""
    _docs(session)


@nox.session
def build(session):
    """Build the project."""
    session.notify("_execute", posargs=([_build], *session.posargs))


def _autobuild(session):
    """Use Sphinx-Autobuild to rebuild the project and open in browser."""
    _autodocs(session)


@nox.session
def autobuild(session):
    """Rebuild the project continuously as source files are changed."""
    session.notify("_execute", posargs=([_autobuild], *session.posargs))


# --- Docs --- #


def _docs(session):
    """Execute the docs build."""
    sphinx_invocation = construct_sphinx_invocation(
        posargs=session.posargs[1:]
    )
    session.run(*sphinx_invocation)


@nox.session
def docs(session):
    """Build the documentation."""
    session.notify("_execute", posargs=([_docs], *session.posargs))


def _autodocs(session):
    """Use Sphinx-Autobuild to rebuild the project and open in browser."""
    session.install("sphinx-autobuild")

    with tempfile.TemporaryDirectory() as destination:
        sphinx_invocation = construct_sphinx_invocation(
            posargs=session.posargs[1:],
            build_dir=destination,
            extra_options=["-a"],
            build_invocation=[
                "sphinx-autobuild",
                "--port=0",
                f"--watch={SOURCE_DIR}",
                "--open-browser",
            ],
        )
        session.run(*sphinx_invocation)


@nox.session
def autodocs(session):
    """Rebuild the docs continuously as source files are changed."""
    session.notify("_execute", posargs=([_autodocs], *session.posargs))


def _patch_theme_css_language_switcher(build_dir):
    """Patch the theme CSS to support styling the language switcher."""
    theme_css_path = (
        build_dir / STATIC_DIR_NAME / "styles" / "pydata-sphinx-theme.css"
    )
    print(f"Modifying {theme_css_path.as_posix()}")

    modify_asset(
        pattern=(
            r"([\{\}\,])([^\{\}\,]*)"
            r"version-switcher__(container|button|menu)"
            r"([^\{\,]*)([\{\,])"
        ),
        replacement=r"\1\2version-switcher__\3\4,\2language-switcher__\3\4\5",
        asset_path=theme_css_path,
    )


def _build_languages(session):
    """Build the docs in multiple languages."""
    languages, posargs = extract_option_values(
        session.posargs[1:], ("--lang", "--language"), split_csv=True
    )
    languages = languages or ALL_LANGUAGES
    _patch_theme_css_language_switcher(HTML_BUILD_DIR)

    for language in languages:
        print(f"\nBuilding {language} translation...\n")
        build_dir = HTML_BUILD_DIR / language
        sphinx_invocation = construct_sphinx_invocation(
            posargs=posargs,
            build_dir=build_dir,
            extra_options=["-D", f"language={language}"],
        )
        session.run(*sphinx_invocation)
        _patch_theme_css_language_switcher(build_dir)


@nox.session(name="build-languages")
def build_languages(session):
    """Build the project in multiple languages (specify with '--lang')."""
    session.notify("_execute", posargs=([_build_languages], *session.posargs))


@nox.session(name="build-multilanguage")
def build_multilanguage(session):
    """Build the project for deployment in all languages."""
    session.notify(
        "_execute", posargs=([_build, _build_languages], *session.posargs)
    )


# ---- Deploy ---- #


def _serve(session=None):
    """Open the docs in a web browser."""
    _serve_docs(session)


def _serve_docs(_session=None):
    """Open the docs in a web browser."""
    webbrowser.open(HTML_INDEX_PATH.as_uri())


@nox.session
def serve(_session):
    """Display the built project."""
    _serve()


@nox.session(name="serve-docs")
def serve_docs(_session):
    """Display the rendered documentation."""
    _serve_docs()


def _prepare_multiversion(_session=None):
    """Execute the pre-deployment steps for multi-version support."""
    # pylint: disable=import-outside-toplevel
    # pylint: disable=import-error

    sys.path.append(str(SCRIPT_DIR))
    import generateredirects
    import safecopy

    latest_version_dir = HTML_BUILD_DIR / str(LATEST_VERSION)
    shutil.copytree(
        HTML_BUILD_DIR, latest_version_dir, copy_function=shutil.move
    )
    safecopy.copy_dir_if_not_existing(
        source_dir=str(LATEST_VERSION),
        target_dir="current",
        base_path=HTML_BUILD_DIR,
        verbose=True,
    )
    generateredirects.generate_redirects(
        canonical_dir="current",
        base_path=HTML_BUILD_DIR,
        verbose=True,
        base_url=BASE_URL,
    )


@nox.session(name="prepare-multiversion")
def prepare_multiversion(_session):
    """Prepare the project for multi-version deployment."""
    _prepare_multiversion()


@nox.session(name="build-deployment")
def build_deployment(session):
    """Build and prepare the project for production deployment."""
    session.notify(
        "_execute",
        posargs=(
            [_build, _build_languages, _prepare_multiversion],
            *session.posargs,
        ),
    )


# ---- Check ---- #


def _install_hooks(session):
    """Run pre-commit install to install the project's hooks."""
    session.run(
        "pre-commit",
        "install",
        "--hook-type",
        "pre-commit",
        "--hook-type",
        "commit-msg",
    )


@nox.session(name="install-hooks")
def install_hooks(session):
    """Install the project's pre-commit hooks."""
    session.notify("_execute", posargs=([_install_hooks],))


def _uninstall_hooks(session):
    """Run pre-commit uninstall to uninstall the project's hooks."""
    session.run(
        "pre-commit",
        "uninstall",
        "--hook-type",
        "pre-commit",
        "--hook-type",
        "commit-msg",
    )


@nox.session(name="uninstall-hooks")
def uninstall_hooks(session):
    """Uninstall the project's pre-commit hooks."""
    session.notify("_execute", posargs=([_uninstall_hooks],))


def _lint(session):
    """Run linting on the project via pre-commit."""
    extra_options = ["--show-diff-on-failure"] if CI else []
    session.run(
        "pre-commit", "run", "--all", *extra_options, *session.posargs[1:]
    )


@nox.session
def lint(session):
    """Lint the project."""
    session.notify("_execute", posargs=([_lint], *session.posargs))


def _linkcheck(session):
    """Run Sphinx linkcheck on the docs."""
    sphinx_invocation = construct_sphinx_invocation(
        posargs=session.posargs[1:], builder="linkcheck"
    )
    session.run(*sphinx_invocation)


@nox.session
def linkcheck(session):
    """Check that links in the project are valid."""
    session.notify("_execute", posargs=([_linkcheck], *session.posargs))


# ---- Translation ---- #


def _build_pot(session):
    """Build the docs with Sphinx -b gettext to extract .pot files."""
    sphinx_invocation = construct_sphinx_invocation(
        posargs=session.posargs[1:], builder=GETTEXT_BUILDER
    )
    session.run(*sphinx_invocation)


@nox.session(name="build-pot")
def build_pot(session):
    """Build the gettext .pot file translation catalogs for the project."""
    session.notify("_execute", posargs=([_build_pot], *session.posargs))


def _copy_pot(_session=None):
    """Copy the built gettext .pot files to the locale directory."""
    if POT_DIR.exists():
        for old_file in POT_DIR.glob("*.pot"):
            old_file.unlink()
    else:
        POT_DIR.mkdir(parents=True)
    for pot_file in GETTEXT_BUILD_DIR.glob("*.pot"):
        print(f"Copying {pot_file.relative_to(GETTEXT_BUILD_DIR).as_posix()}")
        shutil.copy2(pot_file, POT_DIR)


@nox.session(name="copy-pot")
def copy_pot(_session):
    """Update the checked-in gettext pot files with the built ones."""
    _copy_pot()


@nox.session(name="update-pot")
def update_pot(session):
    """Rebuild gettext .pot files and update the existing ones."""
    session.notify(
        "_execute", posargs=([_build_pot, _copy_pot], *session.posargs)
    )


def _update_po(session):
    """Run sphinx-intl update to update po files from pot for languages."""
    session.install("sphinx-intl")

    lang_args = []
    posargs = list(session.posargs[1:])
    if "--all-languages" in posargs:
        posargs.pop(posargs.index("--all-languages"))
        for language in ALL_LANGUAGES:
            lang_args += ["--language", language]
    elif "-l" not in posargs and "--language" not in posargs:
        lang_args += ["--language", SOURCE_LANGUAGE]

    session.run(
        "sphinx-intl",
        "--config",
        CONF_PY,
        "update",
        "--pot-dir",
        POT_DIR,
        "--line-width",
        str(PO_LINE_WIDTH),
        "--no-obsolete",
        *lang_args,
        *posargs,
    )


@nox.session(name="update-po")
def update_po(session):
    """Update gettext .po from .pot (pass "-l LANG" to specify languages)."""
    session.notify("_execute", posargs=([_update_po], *session.posargs))


@nox.session(name="update-po-pot")
def update_po_pot(session):
    """Rebuild & update the pot files, & update the source lang po files."""
    session.notify(
        "_execute",
        posargs=([_build_pot, _copy_pot, _update_po], *session.posargs),
    )

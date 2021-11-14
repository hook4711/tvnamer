#!/usr/bin/env python

"""Main tvnamer utility functionality
"""

import os

import sys
from time import sleep, time

sys.path.append('../tvnamer')

import logging
import warnings

try:
    import readline
except ImportError:
    pass

import json

import tvdb_api
from typing import List, Optional

from tvnamer import cliarg_parser, __version__
from tvnamer.config_defaults import defaults
from tvnamer.config import Config
from tvnamer.files import FileFinder, FileParser, Renamer, _apply_replacements_input
from tvnamer.utils import (
    warn,
    format_episode_numbers,
    make_valid_filename, sizeof_fmt,
)
from tvnamer.data import (
    BaseInfo,
    EpisodeInfo,
    DatedEpisodeInfo,
    NoSeasonEpisodeInfo,
)

from tvnamer.tvnamer_exceptions import (
    ShowNotFound,
    SeasonNotFound,
    EpisodeNotFound,
    EpisodeNameNotFound,
    UserAbort,
    InvalidPath,
    NoValidFilesFoundError,
    SkipBehaviourAbort,
    InvalidFilename,
    DataRetrievalError,
)

from rich import print
from rich.table import Table
from rich import box
from rich.panel import Panel
from rich.layout import Layout
from rich.live import Live
from rich.progress import TimeElapsedColumn
from rich.progress import Progress, BarColumn, TextColumn
from rich.text import Text

import keyboard

LOG = logging.getLogger(__name__)


# Key for use in tvnamer only - other keys can easily be registered at https://thetvdb.com/api-information
TVNAMER_API_KEY = "fb51f9b848ffac9750bada89ecba0225"

MSG_COLOR = "white"
MSG_COLOR_PATH = "cyan"


def truncate_string(str_input, max_length):
    str_end = '...'
    length = len(str_input)
    if length > max_length:
        return str_input[:max_length - len(str_end)] + str_end

    return str_input


def get_move_destination(episode):
    # type: (BaseInfo) -> str
    """Constructs the location to move/copy the file
    """

    # TODO: Write functional test to ensure this valid'ifying works
    def wrap_validfname(fname):
        # type: (str) -> str
        """Wrap the make_valid_filename function as it's called twice
        and this is slightly long..
        """
        if Config["move_files_lowercase_destination"]:
            fname = fname.lower()
        return make_valid_filename(
            fname,
            windows_safe=Config["windows_safe_filenames"],
            custom_blacklist=Config["custom_filename_character_blacklist"],
            replace_with=Config["replace_invalid_characters_with"],
        )

    # Calls make_valid_filename on series name, as it must valid for a filename
    if isinstance(episode, DatedEpisodeInfo):
        dest_dir = Config["move_files_destination_date"] % {
            "seriesname": make_valid_filename(episode.seriesname),
            "year": episode.episodenumbers[0].year,
            "month": episode.episodenumbers[0].month,
            "day": episode.episodenumbers[0].day,
            "originalfilename": episode.originalfilename,
        }
    elif isinstance(episode, NoSeasonEpisodeInfo):
        dest_dir = Config["move_files_destination"] % {
            "seriesname": wrap_validfname(episode.seriesname),
            "episodenumbers": wrap_validfname(
                format_episode_numbers(episode.episodenumbers)
            ),
            "originalfilename": episode.originalfilename,
        }
    elif isinstance(episode, EpisodeInfo):
        dest_dir = Config["move_files_destination"] % {
            "seriesname": wrap_validfname(episode.seriesname),
            "seasonnumber": episode.seasonnumber,
            "episodenumbers": wrap_validfname(
                format_episode_numbers(episode.episodenumbers)
            ),
            "originalfilename": episode.originalfilename,
        }

        dest_dir_alt = Config["move_files_destination_alt"] % {
            "seriesname": wrap_validfname(episode.seriesname),
            "seasonnumber": episode.seasonnumber,
            "episodenumbers": wrap_validfname(
                format_episode_numbers(episode.episodenumbers)
            ),
            "originalfilename": episode.originalfilename,
        }

        if os.path.exists(dest_dir_alt):
            # msg_table.add_row(f"[{MSG_COLOR}]Destination directory does not exist:[/][{MSG_COLOR_PATH}]{dest_dir}[/]")
            # msg_table.add_row(f"[{MSG_COLOR}]Alternative directory exists and will be used:[/][{MSG_COLOR_PATH}]{dest_dir_alt}[/]")
            dest_dir = dest_dir_alt

        #if os.path.exists(dest_dir):
            #msg_table.add_row(f"[{MSG_COLOR}]Destination directory exists:[/][{MSG_COLOR_PATH}]{dest_dir}[/]")
            #print("Destination directory exists: " + dest_dir)
        #elif os.path.exists(dest_dir_alt):
            #msg_table.add_row(f"[{MSG_COLOR}]Destination directory does not exist:[/][{MSG_COLOR_PATH}]{dest_dir}[/]")
            #msg_table.add_row(f"[{MSG_COLOR}]Alternative directory exists and will be used:[/][{MSG_COLOR_PATH}]{dest_dir_alt}[/]")
            #dest_dir = dest_dir_alt
    else:
        raise RuntimeError("Unhandled episode subtype of %s" % type(episode))

    return dest_dir


def do_rename_file(cnamer, new_name):
    # type: (Renamer, str) -> None
    """Renames the file. cnamer should be Renamer instance,
    new_name should be string containing new filename.
    """
    try:
        cnamer.new_path(
            new_fullpath=new_name,
            force=Config["overwrite_destination_on_rename"],
            leave_symlink=Config["leave_symlink"],
        )
    except OSError as e:
        if Config["skip_behaviour"] == "exit":
            warn("Exiting due to error: %s" % e)
            raise SkipBehaviourAbort()
        warn("Skipping file due to error: %s" % e)


def do_move_file(cnamer, dest_dir=None, dest_filepath=None, get_path_preview=False):
    # type: (Renamer, Optional[str], Optional[str], bool) -> Optional[str]
    """Moves file to dest_dir, or to dest_filepath
    """

    if (dest_dir, dest_filepath).count(None) != 1:
        raise ValueError("Specify only dest_dir or dest_filepath")

    if not Config["move_files_enable"]:
        raise ValueError("move_files feature is disabled but do_move_file was called")

    if Config["move_files_destination"] is None:
        raise ValueError(
            "Config value for move_files_destination cannot be None if move_files_enabled is True"
        )

    # DI: Zielverzeichnis muss existieren
    try:
        if not os.path.exists(dest_filepath):
            os.makedirs(dest_filepath, exist_ok=True)
            # print("Created directory %s" % dest_filepath)
    except OSError as e:
        warn("Skipping file due to error (Could not create path): %s" % e)
        return None

    try:
        result = cnamer.new_path(
            new_path=dest_dir,
            new_fullpath=dest_filepath,
            always_move=Config["always_move"],
            leave_symlink=Config["leave_symlink"],
            get_path_preview=get_path_preview,
            force=Config["overwrite_destination_on_move"],
        )

        return result

    except OSError as e:
        if Config["skip_behaviour"] == "exit":
            warn("Exiting due to error: %s" % e)
            raise SkipBehaviourAbort()
        warn("Skipping file due to error: %s" % e)
        return None


def confirm(question, options, default="y"):
    # type: (str, List[str], str) -> str
    """Takes a question (string), list of options and a default value (used
    when user simply hits enter).
    Asks until valid option is entered.
    """
    # Highlight default option with [ ]
    options_chunks = []
    for x in options:
        if x == default:
            x = "[%s]" % x
        if x != "":
            options_chunks.append(x)
    options_str = "/".join(options_chunks)

    while True:
        print(question)
        print("(%s) " % options_str, end="")
        try:
            ans = input().strip()
        except KeyboardInterrupt as errormsg:
            print("\n", errormsg)
            raise UserAbort(errormsg)

        if ans in options:
            return ans
        elif ans == "":
            return default


def do_delete_path(episode, ispreview=False, msg_table=None):

    count = len(os.listdir(episode.filepath))

    try:
        if os.path.exists(episode.filepath) and not(os.path.isfile(episode.fullpath)):
            # print("Deleting %s" % episode.filepath)
            if not ispreview:
                os.removedirs(episode.filepath)
            return True
        elif count == 1 and ispreview:
            #print("Path will be deleted: %s" % episode.filepath)

            return True
        elif os.path.isfile(episode.fullpath):
            #warn("File %s already exists!" % episode.filepath)
            return False
        else:
            return True
    except OSError as e:
        #warn("Path %s could not be deleted!" % episode.filepath)
        #warn("Fehler: %s" % e)
        return False

def process_file(tvdb_instance, episode, table):
    # type: (tvdb_api.Tvdb, BaseInfo, Table) -> None
    """Gets episode name, prompts user for input
    """

    if len(Config["input_filename_replacements"]) > 0:
        replaced = _apply_replacements_input(episode.fullfilename)
        print("# With custom replacements: %s" % replaced)

    # Use force_name option. Done after input_filename_replacements so
    # it can be used to skip the replacements easily
    if Config["force_name"] is not None:
        episode.seriesname = Config["force_name"]

    # print("# Detected series: %s (%s)" % (episode.seriesname, episode.number_string()))

    try:
        episode.populate_from_tvdb(
            tvdb_instance,
            force_name=Config["force_name"],
            series_id=Config["series_id"],
        )
    except (DataRetrievalError, ShowNotFound) as errormsg:
        if Config["always_rename"] and Config["skip_file_on_error"] is True:
            if Config["skip_behaviour"] == "exit":
                warn("Exiting due to error: %s" % errormsg)
                raise SkipBehaviourAbort()
            warn("Skipping file due to error: %s" % errormsg)
            return
        else:
            warn("%s" % errormsg)
    except (SeasonNotFound, EpisodeNotFound, EpisodeNameNotFound) as errormsg:
        # Show was found, so use corrected series name
        if Config["always_rename"] and Config["skip_file_on_error"]:
            if Config["skip_behaviour"] == "exit":
                warn("Exiting due to error: %s" % errormsg)
                raise SkipBehaviourAbort()
            warn("Skipping file due to error: %s" % errormsg)
            return

        warn("%s" % errormsg)

    cnamer = Renamer(episode.fullpath)

    should_rename = False

    if Config["move_files_only"]:

        new_name = episode.fullfilename
        should_rename = True

    else:
        new_name = episode.generate_filename()
        if new_name == episode.fullfilename:
            #print("#" * 20)
            #print("Existing filename is correct: %s" % episode.fullfilename)
            #print("#" * 20)
            table.add_row(episode.fullfilename, truncate_string(new_name, 40),
                          truncate_string(get_move_destination(episode), 55), episode.filesize)
            should_rename = True

        else:
            #print("#" * 20)
            #print("Old filename: %s" % episode.fullfilename)

            if len(Config["output_filename_replacements"]) > 0:
                # Show filename without replacements
                print(
                    "Before custom output replacements: %s"
                    % (episode.generate_filename(preview_orig_filename=True))
                )

            #print(f"{episode.fullfilename} => {new_name}")
            table.add_row(episode.fullfilename, truncate_string(new_name, 40),
                          truncate_string(get_move_destination(episode), 55), episode.filesize)

            if Config["dry_run"]:
                # print("%s will be renamed to %s" % (episode.fullfilename, new_name))
                if Config["move_files_enable"]:
                    #print(
                    #    "%s will be moved to %s"
                    #    % (new_name, get_move_destination(episode))
                    #)

                    do_delete_path(episode, True)
                return
            elif Config["always_rename"]:
                do_rename_file(cnamer, new_name)
                if Config["move_files_enable"]:
                    if Config["move_files_destination_is_filepath"]:
                        do_move_file(cnamer=cnamer, dest_filepath=get_move_destination(episode))
                        do_delete_path(episode)
                    else:
                        do_move_file(cnamer=cnamer, dest_dir=get_move_destination(episode))
                return

            ans = confirm("Rename?", options=["y", "n", "a", "q"], default="y")

            if ans == "a":
                print("Always renaming")
                Config["always_rename"] = True
                should_rename = True
            elif ans == "q":
                print("Quitting")
                raise UserAbort("User exited with q")
            elif ans == "y":
                print("Renaming")
                should_rename = True
            elif ans == "n":
                print("Skipping")
            else:
                print("Invalid input, skipping")

            if should_rename:
                do_rename_file(cnamer, new_name)

    if should_rename and Config["move_files_enable"]:
        new_path = get_move_destination(episode)
        if Config["dry_run"]:
            #print("%s will be moved to %s" % (new_name, get_move_destination(episode)))
            return

        if Config["move_files_destination_is_filepath"]:
            do_move_file(cnamer=cnamer, dest_filepath=new_path, get_path_preview=True)
        else:
            do_move_file(cnamer=cnamer, dest_dir=new_path, get_path_preview=True)

        if not Config["batch"] and Config["move_files_confirmation"]:
            ans = confirm("Move file?", options=["y", "n", "q"], default="y")
        else:
            ans = "y"

        if ans == "y":
            print("Moving file")
            do_move_file(cnamer, new_path)
        elif ans == "q":
            print("Quitting")
            raise UserAbort("user exited with q")


def find_files(paths):
    # type: (List[str]) -> List[str]
    """
    Takes an array of paths and a Table for Messages, returns all files found
    """
    valid_files = []

    for cfile in paths:
        cur = FileFinder(
            cfile,
            with_extension=Config["valid_extensions"],
            filename_blacklist=Config["filename_blacklist"],
            recursive=Config["recursive"],
        )

        try:
            valid_files.extend(cur.find_files())
        except InvalidPath:
            warn("Invalid path: %s" % cfile)

    if len(valid_files) == 0:
        raise NoValidFilesFoundError()

    # Remove duplicate files (all paths from FileFinder are absolute)
    valid_files = list(set(valid_files))

    return valid_files


def create_info_grid(paths, count_files, header_info):
    # type: (List[str], int, Layout) -> None
    """
    Kopfzeile mit Pfad(en) und Anzahl gefundener Episoden erzeugen und anzeigen
    """
    info_grid = Table.grid(expand=True)
    info_grid.add_column(justify="left", ratio=3)
    info_grid.add_column(justify="right")

    info_grid.add_row("[white]Searching:[/] [cyan]" + ", ".join(paths) + "[/]",
                      "[white]Found:[/] [cyan]%d[/] [white]episode" % count_files + ("s" * (count_files > 1)) + "[/]")

    header_info.update(Panel(info_grid, style="cyan", box=box.ROUNDED))


def tvnamer(paths):
    # type: (List[str]) -> None
    """Main tvnamer function, takes an array of paths, does stuff.
    """

    # Layout erzeugen
    layout = create_layout()

    # Tabelle fuer die einzelnen Episoden
    table = create_table()

    episodes_found = []
    size = 0

    for cfile in find_files(paths):
        parser = FileParser(cfile)
        try:
            episode = parser.parse()
        except InvalidFilename as e:
            warn("Invalid filename: %s" % e)
        else:
            if (
                episode.seriesname is None
                and Config["force_name"] is None
                and Config["series_id"] is None
            ):
                warn(
                    "Parsed filename did not contain series name (and --name or --series-id not specified), skipping: %s"
                    % cfile
                )

            else:
                episodes_found.append(episode)
                size += episode.filesize_in_bytes

    if len(episodes_found) == 0:
        raise NoValidFilesFoundError()

    # Informationen im Header anzeigen
    create_info_grid(paths, len(episodes_found), layout.get("header").get("header_info"))

    # Sort episodes by series name, season and episode number
    episodes_found.sort(key=lambda x: x.sortable_info())

    # episode sort order
    if Config["order"] == "dvd":
        dvdorder = True
    else:
        dvdorder = False

    if Config["tvdb_api_key"] is not None:
        LOG.debug("Using custom API key from config")
        api_key = Config["tvdb_api_key"]
    else:
        LOG.debug("Using tvnamer default API key")
        api_key = TVNAMER_API_KEY

    if os.getenv("TVNAMER_TEST_MODE", "0") == "1":
        from .test_cache import get_test_cache_session
        cache = get_test_cache_session()
    else:
        cache = True

    tvdb_instance = tvdb_api.Tvdb(
        interactive=not Config["select_first"],
        search_all_languages=Config["search_all_languages"],
        language=Config["language"],
        dvdorder=dvdorder,
        cache=cache,
        apikey=api_key,
    )

    # Progressbar im Footer erzeugen
    text_column = TextColumn(" [white]Fortschritt:[/]")
    bar_column  = BarColumn(bar_width=None, style="white", complete_style="bright_green", finished_style="green")
    progress    = Progress(text_column, bar_column, "[green]{task.percentage:>3.0f}%[/]", TimeElapsedColumn(), expand=True)

    # Meldung erzeugen
    msg_text = Text("scanning...", justify="center", style="white")

    # Progress und Meldung anzeigen
    layout.get("footer").get("footer_left").update(Panel(progress, box=box.ROUNDED, style="cyan"))
    layout.get("footer").get("footer_right").update(Panel(msg_text, box=box.ROUNDED, style="cyan"))

    # Table anzeigen
    layout.get("body").update(Panel(table, box= box.ROUNDED, style="cyan"))

    with Live(layout, auto_refresh=True, refresh_per_second=2):
        # Informationen zu den Episoden ermitteln
        for episode in progress.track(episodes_found):
            process_file(tvdb_instance, episode, table)

        # Summenzeile ausgeben
        table.add_row(None, None, None, None, end_section=True)
        table.add_row(None, None, Text("Summe:", justify="right", style="bright_yellow"),
                      Text(sizeof_fmt(size), justify="right", style="bright_yellow"))

        # Meldung ausgeben
        msg_text = Text("[m]= move, [r]= rename only, [q]= quit", justify="center", style="bright_yellow")
        layout.get("footer").get("footer_right").update(Panel(msg_text, box=box.ROUNDED, style="cyan"))

    # Warten auf Tastendruck
    wait(layout, episodes_found)


def create_layout():
    # type: () -> Layout
    """
    Erzeugt das Layout fuer die Bildschirmausgaben.
    """
    layout = Layout()

    header = Layout(name="header", size=6)
    body   = Layout(name="body", minimum_size=20)
    footer = Layout(name="footer", size=3)

    layout.split(
        header,
        body,
        footer
    )

    grid = Table.grid(expand=True)
    grid.add_column(justify="center", ratio=1)
    grid.add_row(
        "[b]TVNamer[/b] modified by R.Dion (c)2021", style="white"
    )

    header_title = Layout(name="header_title", size=3)
    header_title.update(Panel(grid, box=box.SIMPLE))

    header_info = Layout(name="header_info", size=3)

    header.split(
        header_title,
        header_info
    )

    footer_left = Layout(name="footer_left", ratio=2)
    footer_right = Layout(name="footer_right")

    layout.get("footer").split_row(
        footer_left,
        footer_right
    )

    return layout


def create_table(show_time=False):
    # type: (bool) -> Table
    """
    Erzeugt die Tabelle fuer die Ausgabe der einzelnen Episoden.
    """
    table = Table(expand=True, box=box.ROUNDED, border_style="bright_black")
    table.add_column("Original", style="bright_yellow", header_style="white bold")
    table.add_column("Neu", style="green", header_style="white bold")
    table.add_column("Ziel", style="cyan", header_style="white bold")
    table.add_column("Grösse", style="white", header_style="white bold", justify="right", max_width=8)

    if show_time:
        table.add_column("Dauer", style="white", header_style="white bold", justify="right", max_width=8)

    return table


def wait(layout, episodes_found):
    # type: (Layout, List[BaseInfo]) -> None
    """
    Warten auf Tastatureingabe des Users.
    """
    keyboard.add_hotkey('m', lambda: move_files(layout, episodes_found))
    keyboard.add_hotkey('r', lambda: move_files(layout, episodes_found, rename_only=True))
    keyboard.wait('q')


def time_convert(sec):
    # type: (float) -> str
    mins = sec // 60
    sec  = sec % 60
    return "%02d:%02d" % (int(mins), int(sec))


def move_files(layout, episodes_found, rename_only=False):
    # type: (Layout, List[BaseInfo], bool) -> None
    """
    Verschieben der Dateien nach Anzeige und Auswahl [m]
    """

    # Prograssbar neu erzeugen und anzeigen
    text_column = TextColumn(" [white]Fortschritt:[/]")
    bar_column = BarColumn(bar_width=None, style="white", complete_style="bright_green", finished_style="green")
    progress = Progress(text_column, bar_column, "[green]{task.percentage:>3.0f}%[/]", TimeElapsedColumn(), expand=True)

    layout.get("footer").get("footer_left").update(Panel(progress, box=box.ROUNDED, style="cyan"))
    layout.update(layout.get("footer"))

    # Tabelle fuer die Verschiebung neu erzeugen und anzeigen
    table = create_table(show_time=True)
    layout.get("body").update(Panel(table, box=box.ROUNDED, style="cyan"))
    layout.update(layout.get("body"))

    with Live(layout, auto_refresh=True, refresh_per_second=2):
        sum_time = 0
        sum_size = 0
        count = 0

        # Alle Episoden durchlaufen und verschieben
        for episode in progress.track(episodes_found):
            count += 1
            msg_text = Text("Moving file #%d (%s)..." % (count, episode.filesize), justify="center", style="white")
            layout.get("footer").get("footer_right").update(Panel(msg_text, box=box.ROUNDED, style="cyan"))

            # Zeitmessung starten
            start_time = time()
            # Renamer erzeugen
            cnamer = Renamer(episode.fullpath)
            # Neuen Dateinamen generieren
            new_name = episode.generate_filename()
            # Datei umbenennen
            do_rename_file(cnamer, new_name)
            # Datei verschieben
            if not rename_only:
                do_move_file(cnamer=cnamer, dest_filepath=get_move_destination(episode), get_path_preview=False)
            # Zeitmessung stoppen
            end_time = time()

            # Dauer berechnen
            elapsed = end_time - start_time
            # Gesamtdauer erhoehen
            sum_time += elapsed
            # Gesamtgroesse erhoehen
            sum_size += episode.filesize_in_bytes

            # Zeile in Table ausgeben
            table.add_row(episode.originalfilename, new_name, get_move_destination(episode),
                          episode.filesize, time_convert(elapsed))

        # Summenzeile ausgeben
        col_text = Text("Gesamt:", justify="right", style="bright_yellow")
        sum_text_time = Text(time_convert(sum_time), justify="right", style="bright_yellow")
        sum_text_size = Text(sizeof_fmt(sum_size), justify="right", style="bright_yellow")

        table.add_row(None, None, None, None, None, end_section=True)
        table.add_row(None, None, col_text, sum_text_size, sum_text_time)

        # Anzeige Meldung
        msg_text = Text("Press [q] to exit", justify="center", style="red")
        layout.get("footer").get("footer_right").update(Panel(msg_text, box=box.ROUNDED, style="cyan"))


def main():
    # type: () -> None
    """Parses command line arguments, displays errors from tvnamer in terminal
    """
    opter = cliarg_parser.get_cli_parser(defaults)

    opts, args = opter.parse_args()

    if opts.show_version:
        print("tvnamer version: %s" % (__version__,))
        print("tvdb_api version: %s" % (tvdb_api.__version__,))
        print("python version: %s" % (sys.version,))
        sys.exit(0)

    if opts.verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )
    else:
        logging.basicConfig()

    # If a config is specified, load it, update the defaults using the loaded
    # values, then reparse the options with the updated defaults.
    default_configuration = os.path.expanduser("~/.config/tvnamer/tvnamer.json")
    old_default_configuration = os.path.expanduser("~/.tvnamer.json")

    if opts.loadconfig is not None:
        # Command line overrides loading ~/.config/tvnamer/tvnamer.json
        config_to_load = opts.loadconfig
    elif os.path.isfile(default_configuration):
        # No --config arg, so load default config if it exists
        config_to_load = default_configuration
    elif os.path.isfile(old_default_configuration):
        # No --config arg and neow defualt config so load old version if it exist
        config_to_load = old_default_configuration
    else:
        # No arg, nothing at default config location, don't load anything
        config_to_load = None

    if config_to_load is not None:
        LOG.info("Loading config: %s" % config_to_load)
        if os.path.isfile(old_default_configuration):
            LOG.warning("WARNING: you have a config at deprecated ~/.tvnamer.json location.")
            LOG.warning("Config must be moved to new location: ~/.config/tvnamer/tvnamer.json")

        try:
            loaded_config = json.load(open(os.path.expanduser(config_to_load)))
        except ValueError as e:
            LOG.error("Error loading config: %s" % e)
            opter.exit(1)
        else:
            # Config loaded, update optparser's defaults and reparse
            defaults.update(loaded_config)
            opter = cliarg_parser.get_cli_parser(defaults)
            opts, args = opter.parse_args()

    # Save config argument
    if opts.saveconfig is not None:
        LOG.info("Saving config: %s" % opts.saveconfig)
        config_to_save = dict(opts.__dict__)
        del config_to_save["saveconfig"]
        del config_to_save["loadconfig"]
        del config_to_save["showconfig"]
        json.dump(
            config_to_save,
            open(os.path.expanduser(opts.saveconfig), "w+"),
            sort_keys=True,
            indent=4,
        )

        opter.exit(0)

    # Show config argument
    if opts.showconfig:
        print(json.dumps(opts.__dict__, sort_keys=True, indent=2))
        return

    # Process values
    if opts.batch:
        opts.select_first = True
        opts.always_rename = True

    # Update global config object
    Config.update(opts.__dict__)

    if Config["move_files_only"] and not Config["move_files_enable"]:
        opter.error(
            "Parameter move_files_enable cannot be set to false while parameter move_only is set to true."
        )

    if Config["titlecase_filename"] and Config["lowercase_filename"]:
        warnings.warn(
            "Setting 'lowercase_filename' clobbers 'titlecase_filename' option"
        )

    if len(args) == 0:
        opter.error("No filenames or directories supplied")

    try:
        tvnamer(paths=sorted(args))
    except NoValidFilesFoundError:
        opter.error("No valid files were supplied")
    except UserAbort as errormsg:
        opter.error(errormsg)
    except SkipBehaviourAbort as errormsg:
        opter.error(errormsg)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
import gi
gi.require_version("Playerctl", "2.0")
from gi.repository import Playerctl, GLib
from gi.repository.Playerctl import Player
import argparse
import logging
import sys
import signal
import json
import html
import os
from typing import List, Optional

logger = logging.getLogger(__name__)

# Constants
PLAYING_STATUS = "Playing"
SPOTIFY_PLAYER = "spotify"
AD_TRACK_ID = ":ad:"

class PlayerManager:
    def __init__(self, selected_player: Optional[str] = None, excluded_players: Optional[str] = None):
        self.manager = Playerctl.PlayerManager()
        self.loop = GLib.MainLoop()
        self.manager.connect("name-appeared", self.on_player_appeared)
        self.manager.connect("player-vanished", self.on_player_vanished)

        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)

        self.selected_player = selected_player
        self.excluded_players = excluded_players.split(',') if excluded_players else []

        self.init_players()

    def signal_handler(self, sig, _frame):
        """Handle shutdown signals gracefully."""
        logger.info(f"Received signal {sig} to stop, exiting")
        self.loop.quit()
    def init_players(self):
        """Initialize existing players."""
        for player_name in self.manager.props.player_names:
            if player_name.name in self.excluded_players:
                continue
            if self.selected_player is not None and self.selected_player != player_name.name:
                logger.debug(f"{player_name.name} is not the filtered player, skipping it")
                continue
            self.init_player(player_name)

    def run(self):
        """Start the main event loop."""
        logger.info("Starting main loop")
        self.loop.run()

    def init_player(self, player_name):
        """Initialize a new player instance."""
        logger.info(f"Initialize new player: {player_name.name}")
        player_instance = Playerctl.Player.new_from_name(player_name)
        player_instance.connect("playback-status", self.on_playback_status_changed, None)
        player_instance.connect("metadata", self.on_metadata_changed, None)
        self.manager.manage_player(player_instance)
        self.on_metadata_changed(player_instance, player_instance.props.metadata)

    def get_players(self) -> List[Player]:
        """Get list of managed players."""
        return self.manager.props.players

    def write_output(self, text: str, player: Player):
        """Write formatted JSON output to stdout."""
        logger.debug(f"Writing output: {text}")

        output = {
            "text": text,
            "class": f"custom-{player.props.player_name}",
            "alt": player.props.player_name
        }

        sys.stdout.write(json.dumps(output) + "\n")
        sys.stdout.flush()

    def clear_output(self):
        """Clear the output by writing an empty line."""
        sys.stdout.write("\n")
        sys.stdout.flush()

    def on_playback_status_changed(self, player: Player, status: str, _=None):
        """Handle playback status changes."""
        logger.debug(f"Playback status changed for player {player.props.player_name}: {status}")
        self.on_metadata_changed(player, player.props.metadata)

    def get_first_playing_player(self) -> Optional[Player]:
        """Get the first player that is currently playing."""
        players = self.get_players()
        logger.debug(f"Getting first playing player from {len(players)} players")
        
        if not players:
            logger.debug("No players found")
            return None
            
        # Reverse order to prefer most recently added players
        for player in reversed(players):
            if player.props.status == PLAYING_STATUS:
                return player
                
        # If none are playing, return the first one
        return players[0]

    def show_most_important_player(self):
        """Show the most important player (playing or first paused)."""
        logger.debug("Showing most important player")
        current_player = self.get_first_playing_player()
        if current_player is not None:
            self.on_metadata_changed(current_player, current_player.props.metadata)
        else:    
            self.clear_output()

    def is_most_important_player(self, player: Player) -> bool:
        """Check if this player should be displayed."""
        current_playing = self.get_first_playing_player()
        return current_playing is None or current_playing.props.player_name == player.props.player_name

    @staticmethod
    def escape_if_string(value) -> str:
        """Escape HTML entities if value is a string."""
        return html.escape(value) if isinstance(value, str) else value

    def on_metadata_changed(self, player: Player, metadata: dict, _=None):
        """Handle metadata changes and update output."""
        logger.debug(f"Metadata changed for player {player.props.player_name}")
        player_name = player.props.player_name
        artist = self.escape_if_string(player.get_artist())
        title = self.escape_if_string(player.get_title())

        track_info = ""

        # Handle Spotify advertisements
        if (player_name == SPOTIFY_PLAYER and 
            "mpris:trackid" in metadata.keys() and 
            AD_TRACK_ID in player.props.metadata["mpris:trackid"]):
            track_info = "Advertisement"
        elif artist and title:
            track_info = f"{artist} - {title}"
        elif title:
            track_info = title

        # Add playback status icons
        if track_info:
            icon = "" if player.props.status == PLAYING_STATUS else ""
            track_info = f"{icon}  {track_info} "
                
        # Only print output if this is the most important player
        if self.is_most_important_player(player) and track_info:
            self.write_output(track_info, player)
        else:
            logger.debug(f"Other player {self.get_first_playing_player().props.player_name} is playing, skipping")

    def on_player_appeared(self, _, player_name):
        """Handle new player appearance."""
        logger.info(f"Player has appeared: {player_name.name}")
        if player_name.name in self.excluded_players:
            logger.debug("New player appeared, but it's in exclude player list, skipping")
            return
        if player_name is not None and (self.selected_player is None or player_name.name == self.selected_player):
            self.init_player(player_name)
        else:
            logger.debug("New player appeared, but it's not the selected player, skipping")

    def on_player_vanished(self, _, player: Player):
        """Handle player disappearance."""
        logger.info(f"Player {player.props.player_name} has vanished")
        self.show_most_important_player()

def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Media player status monitor")

    # Increase verbosity with every occurrence of -v
    parser.add_argument("-v", "--verbose", action="count", default=0,
                       help="Increase verbosity (-v, -vv, -vvv)")
    
    parser.add_argument("-x", "--exclude", help="Comma-separated list of excluded players")

    # Define for which player we're listening
    parser.add_argument("--player", help="Specific player to monitor")

    parser.add_argument("--enable-logging", action="store_true",
                       help="Enable logging to file")

    return parser.parse_args()

def main():
    """Main entry point."""
    arguments = parse_arguments()

    # Initialize logging
    if arguments.enable_logging:
        logfile = os.path.join(os.path.dirname(
            os.path.realpath(__file__)), "media-player.log")
        logging.basicConfig(filename=logfile, level=logging.DEBUG,
                            format="%(asctime)s %(name)s %(levelname)s:%(lineno)d %(message)s")

    # Logging is set by default to WARN and higher.
    # With every occurrence of -v it's lowered by one
    logger.setLevel(max((3 - arguments.verbose) * 10, 0))

    logger.info("Creating player manager")
    if arguments.player:
        logger.info(f"Filtering for player: {arguments.player}")
    if arguments.exclude:
        logger.info(f"Exclude player {arguments.exclude}")

    player_manager = PlayerManager(arguments.player, arguments.exclude)
    player_manager.run()

if __name__ == "__main__":
    main()

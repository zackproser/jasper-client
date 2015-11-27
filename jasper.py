#!/usr/bin/env python2
# -*- coding: utf-8 -*-

import os
import sys
import shutil
import logging

import yaml
import argparse

from client import jasperpath, diagnose, audioengine, brain
from client import pluginstore
from client.conversation import Conversation

# Add jasperpath.LIB_PATH to sys.path
sys.path.append(jasperpath.LIB_PATH)

parser = argparse.ArgumentParser(description='Jasper Voice Control Center')
parser.add_argument('--local', action='store_true',
                    help='Use text input instead of a real microphone')
parser.add_argument('--no-network-check', action='store_true',
                    help='Disable the network connection check')
parser.add_argument('--debug', action='store_true', help='Show debug messages')
list_info = parser.add_mutually_exclusive_group(required=False)
list_info.add_argument('--diagnose', action='store_true',
                       help='Run diagnose and exit')
list_info.add_argument('--list-plugins', action='store_true',
                       help='List plugins and exit')
list_info.add_argument('--list-audio-devices', action='store_true',
                       help='List audio devices and exit')
args = parser.parse_args()

if args.local:
    from client.local_mic import Mic
else:
    from client.mic import Mic


class Jasper(object):
    def __init__(self):
        self._logger = logging.getLogger(__name__)

        # Create config dir if it does not exist yet
        if not os.path.exists(jasperpath.CONFIG_PATH):
            try:
                os.makedirs(jasperpath.CONFIG_PATH)
            except OSError:
                self._logger.error("Could not create config dir: '%s'",
                                   jasperpath.CONFIG_PATH, exc_info=True)
                raise

        # Check if config dir is writable
        if not os.access(jasperpath.CONFIG_PATH, os.W_OK):
            self._logger.critical("Config dir %s is not writable. Jasper " +
                                  "won't work correctly.",
                                  jasperpath.CONFIG_PATH)

        # FIXME: For backwards compatibility, move old config file to newly
        #        created config dir
        old_configfile = os.path.join(jasperpath.LIB_PATH, 'profile.yml')
        new_configfile = jasperpath.config('profile.yml')
        if os.path.exists(old_configfile):
            if os.path.exists(new_configfile):
                self._logger.warning("Deprecated profile file found: '%s'. " +
                                     "Please remove it.", old_configfile)
            else:
                self._logger.warning("Deprecated profile file found: '%s'. " +
                                     "Trying to copy it to new location '%s'.",
                                     old_configfile, new_configfile)
                try:
                    shutil.copy2(old_configfile, new_configfile)
                except shutil.Error:
                    self._logger.error("Unable to copy config file. " +
                                       "Please copy it manually.",
                                       exc_info=True)
                    raise

        # Read config
        self._logger.debug("Trying to read config file: '%s'", new_configfile)
        try:
            with open(new_configfile, "r") as f:
                self.config = yaml.safe_load(f)
        except OSError:
            self._logger.error("Can't open config file: '%s'", new_configfile)
            raise

        try:
            audio_engine_slug = self.config['audio_engine']
        except KeyError:
            audio_engine_slug = 'pyaudio'
            logger.info("audio_engine not specified in profile, using " +
                        "defaults.")
        logger.debug("Using Audio engine '%s'", audio_engine_slug)

        try:
            active_stt_slug = self.config['stt_engine']
        except KeyError:
            active_stt_slug = 'sphinx'
            logger.warning("stt_engine not specified in profile, using " +
                           "defaults.")
        logger.debug("Using STT engine '%s'", active_stt_slug)

        try:
            passive_stt_slug = self.config['stt_passive_engine']
        except KeyError:
            passive_stt_slug = active_stt_slug
        logger.debug("Using passive STT engine '%s'", passive_stt_slug)

        try:
            tts_slug = self.config['tts_engine']
        except KeyError:
            tts_slug = 'espeak-tts'
            logger.warning("tts_engine not specified in profile, using" +
                           "defaults.")
        logger.debug("Using TTS engine '%s'", tts_slug)

        # Load plugins
        self.plugins = pluginstore.PluginStore([jasperpath.PLUGIN_PATH])
        self.plugins.detect_plugins()

        # Initialize AudioEngine
        ae_info = self.plugins.get_plugin(audio_engine_slug,
                                          category='audioengine')
        audio = ae_info.plugin_class(ae_info, self.config)

        # Initialize audio input device
        devices = [device.slug for device in audio.get_devices(
            device_type=audioengine.DEVICE_TYPE_INPUT)]
        try:
            device_slug = self.config['input_device']
        except KeyError:
            device_slug = audio.get_default_device(output=False).slug
            logger.warning("input_device not specified in profile, " +
                           "defaulting to '%s' (Possible values: %s)",
                           device_slug, ', '.join(devices))
        try:
            input_device = audio.get_device_by_slug(device_slug)
            if audioengine.DEVICE_TYPE_INPUT not in input_device.types:
                raise audioengine.UnsupportedFormat(
                    "Audio device with slug '%s' is not an input device"
                    % input_device.slug)
        except (audioengine.DeviceException) as e:
            logger.critical(e.args[0])
            logger.warning('Valid output devices: %s', ', '.join(devices))
            raise

        # Initialize audio output device
        devices = [device.slug for device in audio.get_devices(
            device_type=audioengine.DEVICE_TYPE_OUTPUT)]
        try:
            device_slug = self.config['output_device']
        except KeyError:
            device_slug = audio.get_default_device(output=True).slug
            logger.warning("output_device not specified in profile, " +
                           "defaulting to '%s' (Possible values: %s)",
                           device_slug, ', '.join(devices))
        try:
            output_device = audio.get_device_by_slug(device_slug)
            if audioengine.DEVICE_TYPE_OUTPUT not in output_device.types:
                raise audioengine.UnsupportedFormat(
                    "Audio device with slug '%s' is not an output device"
                    % output_device.slug)
        except (audioengine.DeviceException) as e:
            logger.critical(e.args[0])
            logger.warning('Valid output devices: %s', ', '.join(devices))
            raise

        # Initialize Brain
        self.brain = brain.Brain()
        for info in self.plugins.get_plugins_by_category('speechhandler'):
            try:
                plugin = info.plugin_class(info, self.config)
            except Exception as e:
                self._logger.warning(
                    "Plugin '%s' skipped! (Reason: %s)", info.name,
                    e.message if hasattr(e, 'message') else 'Unknown',
                    exc_info=(
                        self._logger.getEffectiveLevel() == logging.DEBUG))
            else:
                self.brain.add_plugin(plugin)

        active_stt_plugin_info = self.plugins.get_plugin(
            active_stt_slug, category='stt')
        active_stt_plugin = active_stt_plugin_info.plugin_class(
            'default', self.brain.get_all_phrases(), active_stt_plugin_info,
            self.config)

        if passive_stt_slug != active_stt_slug:
            passive_stt_plugin_info = self.plugins.get_plugin(
                passive_stt_slug, category='stt')
        else:
            passive_stt_plugin_info = active_stt_plugin_info

        passive_stt_plugin = passive_stt_plugin_info.plugin_class(
            'keyword', self.brain.get_keyword_phrases(),
            passive_stt_plugin_info, self.config)

        tts_plugin_info = self.plugins.get_plugin(tts_slug, category='tts')
        tts_plugin = tts_plugin_info.plugin_class(tts_plugin_info, self.config)

        # Initialize Mic
        self.mic = Mic(
            input_device, output_device,
            passive_stt_plugin, active_stt_plugin,
            tts_plugin)

        self.conversation = Conversation("JASPER", self.mic, self.brain,
                                         self.config)

    def run(self):
        self.conversation.greet()
        self.conversation.handleForever()

if __name__ == "__main__":

    print("*******************************************************")
    print("*             JASPER - THE TALKING COMPUTER           *")
    print("* (c) 2015 Shubhro Saha, Charlie Marsh & Jan Holthuis *")
    print("*******************************************************")

    logging.basicConfig()
    logger = logging.getLogger()
    logger.getChild("client.stt").setLevel(logging.INFO)

    if args.debug:
        logger.setLevel(logging.DEBUG)

    if not args.no_network_check and not diagnose.check_network_connection():
        logger.warning("Network not connected. This may prevent Jasper from " +
                       "running properly.")

    if args.list_plugins:
        pstore = pluginstore.PluginStore([jasperpath.PLUGIN_PATH])
        pstore.detect_plugins()
        plugins = pstore.get_plugins()
        len_name = max(len(info.name) for info in plugins)
        len_version = max(len(info.version) for info in plugins)
        for info in plugins:
            print("%s %s - %s" % (info.name.ljust(len_name),
                                  ("(v%s)" % info.version).ljust(len_version),
                                  info.description))
        sys.exit(1)
    elif args.list_audio_devices:
        ae = audioengine.PyAudioEngine()
        for device in ae.get_devices():
            device.print_device_info(
                verbose=(logger.getEffectiveLevel() == logging.DEBUG))
        sys.exit(0)
    elif args.diagnose:
        failed_checks = diagnose.run()
        sys.exit(0 if not failed_checks else 1)

    try:
        app = Jasper()
    except Exception:
        logger.error("Error occured!", exc_info=True)
        sys.exit(1)

    app.run()

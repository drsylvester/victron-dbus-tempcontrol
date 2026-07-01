#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import dbus
import dbus.exceptions
from gi.repository import GLib
import os
import logging
from logging.handlers import RotatingFileHandler
import time
import sys
from dbus.mainloop.glib import DBusGMainLoop
import configparser  # for config/ini file

TEMPERATURE_REGISTER_ID = 60891
TEMPERATURE_SERVICE_NAME = 'com.victronenergy.temperature'
SOLAR_CHARGER_PREFIX = 'com.victronenergy.solarcharger.'
ALTERNATOR_CHARGER_PREFIX = 'com.victronenergy.alternator.'

# Sentinel temperature values used when a real reading isn't available.
TEMP_READ_FAILED = -98.0   # VREG call raised / returned garbage
TEMP_NEVER_READ = -99.0    # we haven't successfully read a value yet

# our own packages
sys.path.insert(1, os.path.join(os.path.dirname(__file__), '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python'))
from vedbus import VeDbusService, VeDbusItemImport
from settingsdevice import SettingsDevice


def to_native_type(data):
    # Transform dbus types into native types
    if isinstance(data, dbus.Struct):
        return tuple(to_native_type(x) for x in data)
    elif isinstance(data, dbus.Array):
        return [to_native_type(x) for x in data]
    elif isinstance(data, dbus.Dictionary):
        return dict((to_native_type(k), to_native_type(v)) for (k, v) in data.items())
    elif isinstance(data, dbus.Double):
        return float(data)
    elif isinstance(data, dbus.Boolean):
        return bool(data)
    elif isinstance(data, (dbus.String, dbus.ObjectPath)):
        return str(data)
    elif isinstance(data, dbus.Signature):
        return str(data)
    else:
        return int(data)


def get_system_bus(private=True):
    return dbus.SessionBus(private=private) if 'DBUS_SESSION_BUS_ADDRESS' in os.environ else dbus.SystemBus(private=private)


class BaseTempControl:
    """
    Shared logic for publishing a temperature reading (read via VREG over
    VregLink) as a com.victronenergy.temperature dbus service.

    Subclasses just set: kind_label, settings_path_prefix, default_name_fmt,
    and service_name_attr for logging.
    """

    kind_label = 'Device'                       # e.g. "MPPT" / "Alt"
    settings_path_prefix = '/Settings/TempCtrl'  # overridden per subclass
    default_name_fmt = '%s%02d Temperature'

    def __init__(self, servicename, deviceinstance, id, unit_index):
        logging.debug('Initialize %s TempControl Service...', self.kind_label)

        self.settings = None
        self.id = id
        self.unit_index = unit_index
        self.deviceinstance = deviceinstance
        self.temperature = None

        self.dbusConn = get_system_bus()
        self._serial_item = VeDbusItemImport(self.dbusConn, id, '/Serial')
        self._vreg_obj = self.dbusConn.get_object(id, '/Devices/0/VregLink')

        self._init_device_settings(deviceinstance)
        self.read_temperature()

        self._dbusservice = VeDbusService(
            "{}.can_{:02d}".format(servicename, deviceinstance), bus=self.dbusConn, register=False
        )
        self._dbusservice.add_path('/DeviceInstance', deviceinstance)
        self._dbusservice.add_path('/FirmwareVersion', 'v1.0')
        self._dbusservice.add_path('/DataManagerVersion', '1.0')
        self._dbusservice.add_path('/Serial', self._serial_item.get_value())
        self._dbusservice.add_path('/Mgmt/Connection', 'Ve.Can')
        self._dbusservice.add_path('/ProductName', '{} Temperature'.format(self.kind_label))
        self._dbusservice.add_path('/ProductId', 0)
        self._dbusservice.add_path(
            '/CustomName', self.settings['/Customname'], writeable=True, onchangecallback=self.customnameChanged
        )
        self._dbusservice.add_path('/Temperature', None, gettextcallback=lambda p, v: str(v) + 'C')
        self._dbusservice.add_path('/Status', 0)
        self._dbusservice.add_path(
            '/TemperatureType', self.settings['/TemperatureType'], writeable=True, onchangecallback=self.tempTypeChanged
        )
        self._dbusservice.add_path('/Connected', 1)
        self._dbusservice.register()

    def _init_device_settings(self, deviceinstance):
        if self.settings:
            return

        path = '{}/{}'.format(self.settings_path_prefix, deviceinstance)

        SETTINGS = {
            '/Customname': [path + '/CustomName', self.default_name_fmt % (self.kind_label, self.unit_index), 0, 0],
            '/TemperatureType': [path + '/TemperatureType', 2, 0, 0],
        }

        self.settings = SettingsDevice(self.dbusConn, SETTINGS, self._setting_changed)

    def tempTypeChanged(self, path, val):
        self.settings['/TemperatureType'] = val
        return True

    def customnameChanged(self, path, val):
        self.settings['/Customname'] = val
        return True

    def _setting_changed(self, setting, oldvalue, newvalue):
        logging.info("setting changed, setting: %s, old: %s, new: %s", setting, oldvalue, newvalue)

        if setting == '/Customname':
            self._dbusservice['/CustomName'] = newvalue
        if setting == '/TemperatureType':
            self._dbusservice['/TemperatureType'] = newvalue

    def read_temperature(self):
        """Read temperature via VREG. Sets self.temperature; returns True on success."""
        try:
            ret = self._vreg_obj.get_dbus_method('GetVreg', 'com.victronenergy.VregLink')(TEMPERATURE_REGISTER_ID)
            if not ret or len(ret) < 2:
                raise ValueError(f"Bad VREG response: {ret}")

            data = to_native_type(ret[1])
            if not isinstance(data, (list, tuple)) or len(data) < 2:
                raise ValueError(f"Bad payload: {data}")

            # Byte order verified correct against device: data[0]=low byte, data[1]=high byte.
            self.temperature = (data[1] * 256 + data[0]) / 100
            return True

        except (dbus.exceptions.DBusException, ValueError) as e:
            logging.exception("GetVreg failed for %s", self.id)
            self.temperature = TEMP_READ_FAILED
            return False

    def _publish_temperature(self):
        # Use "is None" rather than falsy-check so a genuine 0.0C reading
        # isn't mistaken for "no reading yet".
        value = self.temperature if self.temperature is not None else TEMP_NEVER_READ
        self._dbusservice['/Temperature'] = value
        logging.info("%s%02d Temperature: %.02f", self.kind_label, self.unit_index, value)

    def update(self):
        try:
            logging.info("Updating %s %02d", self.kind_label, self.unit_index)
            self.read_temperature()
            self._publish_temperature()
        except Exception:
            logging.exception("%s update failed for %s", self.kind_label, self.id)
        return True


class MpptTempControl(BaseTempControl):
    kind_label = 'MPPT'
    settings_path_prefix = '/Settings/MPPTTempCtrl'
    default_name_fmt = '%s%02d Temperatur'

    def __init__(self, servicename, deviceinstance, id, mpptid):
        # kept for backwards compatibility with callers using mpptid=
        self.mpptpower = 0
        super().__init__(servicename, deviceinstance, id, mpptid)
        self._power_item = VeDbusItemImport(self.dbusConn, id, '/Yield/Power')

    def read_power(self):
        self.mpptpower = self._power_item.get_value()
        logging.info("MPPT power %s", self.mpptpower)

    def update(self):
        try:
            logging.info("Updating MPPT %02d", self.unit_index)
            self.read_temperature()
            self.read_power()
            self._publish_temperature()
        except Exception:
            logging.exception("MPPT update failed for %s", self.id)
        return True


class AlternatorTempControl(BaseTempControl):
    kind_label = 'Alt'
    settings_path_prefix = '/Settings/AltTempCtrl'
    default_name_fmt = '%s%02d Temperature'


def getConfig():
    config = configparser.ConfigParser()
    config.read("%s/config.ini" % (os.path.dirname(os.path.realpath(__file__))))
    return config


def discover_chargers(bus, dbus_path_prefix):
    proxy = bus.get_object('org.freedesktop.DBus', '/org/freedesktop/DBus')
    iface = dbus.Interface(proxy, 'org.freedesktop.DBus')
    names = iface.ListNames()
    return sorted(str(n) for n in names if str(n).startswith(dbus_path_prefix))


def discover_solar_chargers(bus):
    return discover_chargers(bus, SOLAR_CHARGER_PREFIX)


def discover_alternator_chargers(bus):
    return discover_chargers(bus, ALTERNATOR_CHARGER_PREFIX)


def main():
    print(" *********************************************** ")
    print(" T E M P C O N T R O L   M A I N   S T A R T E D ")
    print(" *********************************************** ")
    print(" ")

    logHandler = RotatingFileHandler(
        "%s/current.log" % (os.path.dirname(os.path.realpath(__file__))),
        mode='a', maxBytes=5 * 1024 * 1024, backupCount=2, encoding=None, delay=0
    )

    logging.basicConfig(
        format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=logging.INFO,
        handlers=[logHandler, logging.StreamHandler()],
    )

    config = getConfig()

    try:
        startupDelay = int(config['DEFAULT'].get('startupDelay', '10'))
        updateInterval = int(config['DEFAULT']['updateInterval'])
        deviceInstanceBase = int(config['DEFAULT'].get('deviceinstancebase', '24'))
        alternatorInstanceBase = int(config['DEFAULT'].get('alternatorinstancebase', '26'))
    except KeyError as e:
        logging.error("Missing required config.ini setting: %s", e)
        sys.exit(1)
    except ValueError as e:
        logging.error("Invalid value in config.ini: %s", e)
        sys.exit(1)

    DBusGMainLoop(set_as_default=True)

    dbusservice = {}
    mainloop = GLib.MainLoop()

    if startupDelay > 0:
        time.sleep(startupDelay)

    dbusConn = get_system_bus()

    chargers = discover_solar_chargers(dbusConn)
    logging.info("Discovered %d solar charger(s): %s", len(chargers), chargers)

    alternators = discover_alternator_chargers(dbusConn)
    logging.info("Discovered %d alternator charger(s): %s", len(alternators), alternators)

    if not chargers and not alternators:
        logging.error("No solar chargers and no alternators found on DBus — exiting")
        sys.exit(1)

    for i, charger_id in enumerate(chargers):
        mpptid = i + 1
        deviceinstance = deviceInstanceBase + i
        key = 'mppt-%02d' % mpptid
        dbusservice[key] = MpptTempControl(
            mpptid=mpptid, servicename=TEMPERATURE_SERVICE_NAME, deviceinstance=deviceinstance, id=charger_id
        )
        GLib.timeout_add(updateInterval, dbusservice[key].update)
        dbusservice[key].update()

    for i, alternator_id in enumerate(alternators):
        alternatorid = i + 1
        alternatorinstance = alternatorInstanceBase + i
        key = 'alt-%02d' % alternatorid
        dbusservice[key] = AlternatorTempControl(
            unit_index=alternatorid, servicename=TEMPERATURE_SERVICE_NAME, deviceinstance=alternatorinstance, id=alternator_id
        )
        GLib.timeout_add(updateInterval, dbusservice[key].update)
        dbusservice[key].update()

    mainloop.run()


if __name__ == "__main__":
    main()

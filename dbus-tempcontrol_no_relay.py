#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import dbus
from gi.repository import GLib
import pprint
import os
import logging
from logging.handlers import RotatingFileHandler
import sys
from dbus.mainloop.glib import DBusGMainLoop
import collections
import optparse
import configparser # for config/ini file


# our own packages
sys.path.insert(1, os.path.join(os.path.dirname(__file__), '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python'))
from vedbus import VeDbusService, VeDbusItemExport, VeDbusItemImport
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
                return str(Signature(data))                                                                                    
        else:
                return int(data)  




class MpptTempControl():
    def __init__(self, servicename, deviceinstance, id, mpptid):
        logging.debug('Initialize MpptTempControl Service...')


        _c = lambda p, v: (str(v) + 'C')
        self.settings = None 
        self.id = id
        self.mpptid = mpptid
        self.mppt01power = 0
        self.deviceinstance = deviceinstance
        self.dbusConn = dbus.SessionBus(private=True) if 'DBUS_SESSION_BUS_ADDRESS' in os.environ else dbus.SystemBus(private=True)
        self.mppt01serial = VeDbusItemImport(self.dbusConn, id, '/Serial')
        self.mppt01tempObj = self.dbusConn.get_object(id, '/Devices/0/VregLink')
        self.mppt01powerObj = VeDbusItemImport(self.dbusConn,id,'/Yield/Power')
        self._init_device_settings(deviceinstance)
        self.readMppt01Temp()

        self._dbusserviceMppt01 = VeDbusService("{}.can_{:02d}".format(servicename, deviceinstance), bus=self.dbusConn, register=False)
        self._dbusserviceMppt01.add_path('/DeviceInstance', deviceinstance)
        self._dbusserviceMppt01.add_path('/FirmwareVersion', 'v1.0')
        self._dbusserviceMppt01.add_path('/DataManagerVersion', '1.0')
        self._dbusserviceMppt01.add_path('/Serial', self.mppt01serial.get_value())
        self._dbusserviceMppt01.add_path('/Mgmt/Connection', 'Ve.Can')
        self._dbusserviceMppt01.add_path('/ProductName', 'MPPT Temperature')
        self._dbusserviceMppt01.add_path('/ProductId', 0) 
        self._dbusserviceMppt01.add_path('/CustomName', self.settings['/Customname'], writeable=True, onchangecallback=self.customnameChanged)
        self._dbusserviceMppt01.add_path('/Temperature', None, gettextcallback=_c)
        self._dbusserviceMppt01.add_path('/Status', 0)
        self._dbusserviceMppt01.add_path('/TemperatureType', self.settings['/TemperatureType'], writeable=True, onchangecallback=self.tempTypeChanged)
        self._dbusserviceMppt01.add_path('/Connected', 1)
        self._dbusserviceMppt01.register()




    def _init_device_settings(self, deviceinstance):
        if self.settings:
            return

        path = '/Settings/MPPTTempCtrl/{}'.format(deviceinstance)

        SETTINGS = {
            '/Customname':  [path + '/CustomName', 'MPPT%02d Temperatur' % self.mpptid, 0, 0],
            '/TemperatureType': [path+'/TemperatureType', 2, 0, 0]
        }

        self.settings = SettingsDevice(self.dbusConn, SETTINGS, self._setting_changed)

    def tempTypeChanged(self, path, val):
        self.settings['/TemperatureType'] = val
        return True

    def customnameChanged(self, path, val):
        self.settings['/Customname'] = val
        return True
  
    def _setting_changed(self, setting, oldvalue, newvalue):
        logging.info("setting changed, setting: %s, old: %s, new: %s" % (setting, oldvalue, newvalue))

        if setting == '/Customname':
          self._dbusserviceMppt01['/CustomName'] = newvalue
        if setting == '/TemperatureType':
          self._dbusserviceMppt01['/TemperatureType'] = newvalue

    def readMppt01Temp(self):
        args = [60891]
        ret = self.mppt01tempObj.get_dbus_method('GetVreg','com.victronenergy.VregLink')(*args) 
        data = to_native_type(ret[1])
        self.mppt01temp = (data[1]*256+data[0])/100 

    def readMppt01Power(self):
        self.mppt01power = self.mppt01powerObj.get_value()
        logging.info("Mppt power %d" % self.mppt01power)
    
    def update(self):
        self.readMppt01Temp()
        self.readMppt01Power()
        self._dbusserviceMppt01['/Temperature'] = self.mppt01temp
        logging.info("MPPT%02d Temperature: %.02f" % (self.mpptid , self.mppt01temp))
        return True


class AlternatorTempControl():
    def __init__(self, servicename, deviceinstance, id, alternatorid):
        logging.debug('Initialize AlternatorTempControl Service...')


        _c = lambda p, v: (str(v) + 'C')
        self.settings = None 
        self.id = id
        self.alternatorid = alternatorid
        self.deviceinstance = deviceinstance
        self.dbusConn = dbus.SessionBus(private=True) if 'DBUS_SESSION_BUS_ADDRESS' in os.environ else dbus.SystemBus(private=True)
        self.alternator01serial = VeDbusItemImport(self.dbusConn, id, '/Serial')
        self.alternator01tempObj = self.dbusConn.get_object(id, '/Devices/0/VregLink')
        self._init_device_settings(deviceinstance)
        self.readAlternator01Temp()

        self._dbusservice01 = VeDbusService("{}.can_{:02d}".format(servicename, deviceinstance), bus=self.dbusConn, register=False)
        self._dbusservice01.add_path('/DeviceInstance', deviceinstance)
        self._dbusservice01.add_path('/FirmwareVersion', 'v1.0')
        self._dbusservice01.add_path('/DataManagerVersion', '1.0')
        self._dbusservice01.add_path('/Serial', self.alternator01serial.get_value())
        self._dbusservice01.add_path('/Mgmt/Connection', 'Ve.Can')
        self._dbusservice01.add_path('/ProductName', 'Alternator Temperature')
        self._dbusservice01.add_path('/ProductId', 0) 
        self._dbusservice01.add_path('/CustomName', self.settings['/Customname'], writeable=True, onchangecallback=self.customnameChanged)
        self._dbusservice01.add_path('/Temperature', None, gettextcallback=_c)
        self._dbusservice01.add_path('/Status', 0)
        self._dbusservice01.add_path('/TemperatureType', self.settings['/TemperatureType'], writeable=True, onchangecallback=self.tempTypeChanged)
        self._dbusservice01.add_path('/Connected', 1)
        self._dbusservice01.register()




    def _init_device_settings(self, deviceinstance):
        if self.settings:
            return

        path = '/Settings/AltTempCtrl/{}'.format(deviceinstance)

        SETTINGS = {
            '/Customname':  [path + '/CustomName', 'Alt%02d Temperature' % self.alternatorid, 0, 0],
            '/TemperatureType': [path+'/TemperatureType', 2, 0, 0]
        }

        self.settings = SettingsDevice(self.dbusConn, SETTINGS, self._setting_changed)

    def tempTypeChanged(self, path, val):
        self.settings['/TemperatureType'] = val
        return True

    def customnameChanged(self, path, val):
        self.settings['/Customname'] = val
        return True
  
    def _setting_changed(self, setting, oldvalue, newvalue):
        logging.info("setting changed, setting: %s, old: %s, new: %s" % (setting, oldvalue, newvalue))

        if setting == '/Customname':
          self._dbusservice01['/CustomName'] = newvalue
        if setting == '/TemperatureType':
          self._dbusservice01['/TemperatureType'] = newvalue

    def readAlternator01Temp(self):
        args = [60891]
        ret = self.alternator01tempObj.get_dbus_method('GetVreg','com.victronenergy.VregLink')(*args) 
        data = to_native_type(ret[1])
        self.alternator01temp = (data[1]*256+data[0])/100 
    
    def update(self):
        self.readAlternator01Temp()
        self._dbusservice01['/Temperature'] = self.alternator01temp
        logging.info("Alt%02d Temperature: %.02f" % (self.alternatorid , self.alternator01temp))
        return True


def getConfig():
    config = configparser.ConfigParser()
    config.read("%s/config.ini" % (os.path.dirname(os.path.realpath(__file__))))
    return config;


def discover_solar_chargers():
    bus = dbus.SessionBus() if 'DBUS_SESSION_BUS_ADDRESS' in os.environ else dbus.SystemBus()
    proxy = bus.get_object('org.freedesktop.DBus', '/org/freedesktop/DBus')
    iface = dbus.Interface(proxy, 'org.freedesktop.DBus')
    names = iface.ListNames()
    return sorted(str(n) for n in names if str(n).startswith('com.victronenergy.solarcharger.'))

def discover_alternator_chargers():
    bus = dbus.SessionBus() if 'DBUS_SESSION_BUS_ADDRESS' in os.environ else dbus.SystemBus()
    proxy = bus.get_object('org.freedesktop.DBus', '/org/freedesktop/DBus')
    iface = dbus.Interface(proxy, 'org.freedesktop.DBus')
    names = iface.ListNames()
    return sorted(str(n) for n in names if str(n).startswith('com.victronenergy.alternator.'))

def main():
        print (" *********************************************** ")
        print (" T E M P C O N T RO L   M A I N   S T A R T E D   ")
        print (" *********************************************** ")
        print (" ")
        logHandler = RotatingFileHandler("%s/current.log" % (os.path.dirname(os.path.realpath(__file__))), mode='a', maxBytes=5*1024*1024, 
                                 backupCount=2, encoding=None, delay=0)

        logging.basicConfig( format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
                             datefmt='%Y-%m-%d %H:%M:%S',
                             level=logging.INFO,
                             handlers=[
                                logHandler,
                                #logging.FileHandler("%s/current.log" % (os.path.dirname(os.path.realpath(__file__)))),
                                logging.StreamHandler()
                             ])

        config = getConfig()

        DBusGMainLoop(set_as_default=True)

        dbusservice = {}

        mainloop = GLib.MainLoop()

        updateInterval = int(config['DEFAULT']['updateInterval'])
        deviceInstanceBase = int(config['DEFAULT'].get('deviceinstancebase', '24'))
        alternatorInstanceBase = int(config['DEFAULT'].get('alternatorinstancebase', '26'))

        chargers = discover_solar_chargers()
        logging.info("Discovered %d solar charger(s): %s" % (len(chargers), chargers))

        alternators = discover_alternator_chargers()
        logging.info("Discovered %d alternator charger(s): %s" % (len(alternators), alternators))

        if not chargers and not alternators:
            logging.error("No solar chargers and no alternators found on DBus — exiting")
            sys.exit(1)

        if chargers:
            for i, charger_id in enumerate(chargers):
                mpptid = i + 1
                deviceinstance = deviceInstanceBase + i
                dbusservice['%02d' % mpptid] = MpptTempControl(mpptid=mpptid, servicename='com.victronenergy.temperature', deviceinstance=deviceinstance, id=charger_id)
                GLib.timeout_add(updateInterval, dbusservice['%02d' % mpptid].update)
                dbusservice['%02d' % mpptid].update()

        if alternators:
            for i, alternator_id in enumerate(alternators):
                alternatorid = i + 1
                alternatorinstance = alternatorInstanceBase + i
                dbusservice['%02d' % alternatorid] = AlternatorTempControl(alternatorid=alternatorid, servicename='com.victronenergy.temperature', deviceinstance=deviceinstance, id=alternator_id)
                GLib.timeout_add(updateInterval, dbusservice['%02d' % alternatorid].update)
                dbusservice['%02d' % alternatorid].update()

        mainloop.run()

if __name__ == "__main__":
        main()

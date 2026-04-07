from PySide6.QtCore import QMetaObject
from plainpod.mpris import MprisRootAdaptor, MprisPlayerAdaptor
import sys

meta = MprisRootAdaptor.staticMetaObject
print("Root Class DBus info:")
found = False
for i in range(meta.classInfoCount()):
    info = meta.classInfo(i)
    print(f"  {info.name()} : {info.value()}")
    if info.name() == "D-Bus Interface": found = True
if not found:
    print("WARNING: No D-Bus interface configured for MprisRootAdaptor!")

meta2 = MprisPlayerAdaptor.staticMetaObject
print("Player Class DBus info:")
found2 = False
for i in range(meta2.classInfoCount()):
    info = meta2.classInfo(i)
    print(f"  {info.name()} : {info.value()}")
    if info.name() == "D-Bus Interface": found2 = True
if not found2:
    print("WARNING: No D-Bus interface configured for MprisPlayerAdaptor!")

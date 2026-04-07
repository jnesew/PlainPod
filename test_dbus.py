import sys
from PySide6.QtCore import ClassInfo
from PySide6.QtDBus import QDBusAbstractAdaptor

class TestAdaptor(QDBusAbstractAdaptor):
    ClassInfo({"D-Bus Interface": "org.mpris.MediaPlayer2"})

print("dir(TestAdaptor): ")
print(dir(TestAdaptor))
print(getattr(TestAdaptor, '__classinfo__', None))

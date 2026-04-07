from PySide6.QtCore import QMetaObject, ClassInfo
from PySide6.QtDBus import QDBusAbstractAdaptor
import sys

try:
    @ClassInfo({"D-Bus Interface": "org.test.1"})
    class Test1(QDBusAbstractAdaptor): pass
    print("Test1 meta:", Test1.staticMetaObject.classInfoCount())
except Exception as e: print("Test1 failed:", e)

try:
    @ClassInfo("D-Bus Interface", "org.test.2")
    class Test2(QDBusAbstractAdaptor): pass
    print("Test2 meta:", Test2.staticMetaObject.classInfoCount())
except Exception as e: print("Test2 failed:", e)

try:
    @ClassInfo(name="D-Bus Interface", value="org.test.3")
    class Test3(QDBusAbstractAdaptor): pass
    print("Test3 meta:", Test3.staticMetaObject.classInfoCount())
except Exception as e: print("Test3 failed:", e)

try:
    class Test4(QDBusAbstractAdaptor):
        __classinfo__ = {b"D-Bus Interface": b"org.test.4"}
    print("Test4 meta:", Test4.staticMetaObject.classInfoCount())
except Exception as e: print("Test4 failed:", e)

try:
    class Test5(QDBusAbstractAdaptor):
        _classinfo = {"D-Bus Interface": "org.test.5"}
    print("Test5 meta:", Test5.staticMetaObject.classInfoCount())
except Exception as e: print("Test5 failed:", e)


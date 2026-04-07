import sys
from PySide6.QtCore import Property, QObject

class TestProps(QObject):
    @Property(int)
    def prop1(self): return 0
    @Property("qlonglong")
    def prop2(self): return 0
    @Property("qint64")
    def prop3(self): return 0

meta = TestProps.staticMetaObject
for i in range(meta.propertyCount()):
    prop = meta.property(i)
    print(prop.name(), "->", prop.typeName())

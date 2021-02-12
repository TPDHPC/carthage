from carthage.dependency_injection import Injector, InjectionKey, inject_autokwargs
from .implementation import ModelingBase, InjectableModelType, ModelingContainer, NSFlags
from .utils import setattr_default
import typing
from ..dependency_injection import InjectionKey


class ModelingDecoratorWrapper:

    subclass: type = ModelingBase
    name: str

    def __init__(self, value):
        self.value = value

    def __repr__(self):
        return f'{self.__class__.name}({repr(self.value)})'

    def handle(self, cls, ns, k, state):
        if not issubclass(cls, self.subclass):
            raise TypeError(f'{self.__class__.name} decorator only for subclasses of {self.cls.__name__}')
        state.value = self.value
        if isinstance(self.value, ModelingDecoratorWrapper):
            if self.value.handle(cls, ns, k, state):
                raise TypeError("A decorator that suppresses assignment must be outermost")
            else:
                self.value = state.value #in case superclasses care

class injector_access(ModelingDecoratorWrapper):

    '''Usage::

        val = injector_access("foo")

    At runtime, ``model_instance.val`` will be memoized to ``model_instance.injector.get_instance("foo")``.

    '''

    #Unlike most ModelingDecorators we want to get left in the
    #namespace.  The only reason this is a ModelingDecorator is so we
    #can clear the inject_by_name flag to avoid namespace polution and
    #to minimize the probability of a circular references from
    #something like ``internet = injector_access("internet")``

    key: InjectionKey
    name: str
    target: type

    def __init__(self, key, target = None):
        super().__init__(key)
        if not isinstance(key, InjectionKey):
            key = InjectionKey(key, _ready = False)
        if key.ready is None: key = InjectionKey(key, _ready = False)
        #: The injection key to be accessed.
        self.key = key
        self.target = target
        # Our __call__method needs an injector
        inject_autokwargs(injector = Injector)(self)

    def handle(self, cls, ns, k, state):
        super().handle(cls, ns, k, state)
        state.value = self
        state.flags &= ~NSFlags.inject_by_name

    def __get__(self, inst, owner):
        if inst is None:
            if self.target: return self.target
            return self
        res = inst.injector.get_instance(self.key)
        setattr(inst, self.name, res)
        return res

    def __set_name__(self, owner, name):
        self.name = name

    def __call__(self, injector: Injector):
        return injector.get_instance(self.key)
    def __repr__(self):
        return f'injector_access({repr(self.key)})'




class ProvidesDecorator(ModelingDecoratorWrapper):
    subclass = InjectableModelType
    name = "provides"

    def __init__(self, value, *keys):
        super().__init__(value)
        self.keys = keys

    def handle(self, cls, ns, k, state):
        super().handle(cls, ns, k, state)
        state.extra_keys.extend(self.keys)

def provides(*keys):
    '''Indicate that the decorated value provides these InjectionKeys'''
    keys = list(map( lambda k: InjectionKey(k), keys))
    def wrapper(val):
        try:
            setattr_default(val, '__provides_dependencies_for__', [])
            val.__provides_dependencies_for__ = keys+val.__provides_dependencies_for__
            return val
        except:

            return ProvidesDecorator(val, *keys)
    return wrapper

class DynamicNameDecorator(ModelingDecoratorWrapper):

    name = "dynamic_name"

    def __init__(self, value, new_name):
        super().__init__(value)
        self.new_name = new_name

    def handle(self, cls, ns, k, state):
        super().handle(cls, ns, k, state)
        value = self.value
        if hasattr(value, '__qualname__'):
            value.__qualname__ = ns['__qualname__']+'.'+self.new_name
            value.__name__ = self.new_name
        state.value = value
        ns[self.new_name] = state.instantiate_value(self.new_name)
        return True

def dynamic_name(name):
    '''A decorator to be used in a modeling type to supply a dynamic name.  Example::

        for i in range(3):
            @dynamic_name(f'i{i+1}')
    class ignored: square = i*i

    Will define threevariables i1 through i3, with values of squares (i1 = 0, i2 =1, i3 = 4).
    '''
    def wrapper(val):
        return DynamicNameDecorator(val, name)
    return wrapper


def globally_unique_key(
        self,
        key: typing.Union[InjectionKey, typing.Callable[[object], InjectionKey]],
                          ):
    '''Decorate a value to indicate that *key* is a globally unique
    :class:`~InjectionKey` that should provide the given value.
    Globally unique keys are not extended with additional constraints
    when propagated up through :class:`~ModelingContainers`.

    :param key:  A callback that maps the value to a key.  Alternatively, simply the :class:`~InjectionKey` to use.

    '''
    def wrapper(val):
        nonlocal key
        if callable(key):
            key = key(val)
            val.__globally_unique_key__ = key
            return val
    return wrapper


class FlagClearDecorator(ModelingDecoratorWrapper):

    def __init__(self, value, flag: NSFlags):
        super().__init__(value)
        self.flag = flag
    def handle(self, cls, ns, k, state):
        super().handle(cls, ns, k, state)
        state.flags &= ~self.flag

def no_instantiate():
    def wrapper(val):
        return FlagClearDecorator(val, NSFlags.instantiate_on_access)
    return wrapper

def no_close():
    def wrapper(val):
        return FlagClearDecorator(val, NSFlags.close)
    return wrapper

def allow_multiple():
    raise NotImplementedError("Need to write FlagSetDecorator")


__all__ = ["ModelingDecoratorWrapper", "provides", 'dynamic_name',
           'injector_access', 'no_instantiate',
           'allow_multiple', 'no_close',
           'globally_unique_key',
           ]

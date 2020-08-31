import re
import numbers
import six
import inspect

import cProfile
import pstats

from pprint import pprint

from Athena import AtUtils
from Athena import AtConstants


class ProcessDataDescriptor(object):

    def __init__(self):
        self.__DATA = {}

    def __get__(self, instance, cls):
        if cls in self.__DATA:
            return self.__DATA[cls]

        raise AttributeError('Object {0} does not have any data for attribute {1}', instance.__name__, self.__name)

    def __del__(self):
        raise NotImplementedError('Unable to delete Process Data')


class ProcessMeta(type):

    def __new__(self, className, bases, attrs):
        return super(ProcessMeta, self).__new__(className, bases, attrs)


class Process(object):
    """Abstract class from which any Athena User Process have to inherit.

    The Process object define default instance attributes for user to use and that are managed through the `automatic`
    decorator.
    It also comes with some methods to manage the internal feedback and the potentially connected QProgressbar.
    There is 3 not implemented methods to override if needed (`check`, `fix` and `tool`)
    """

    __NON_OVERRIDABLE_ATTRIBUTES = \
    {
        '_resetThreads',
        '_clearFeedback',
        'addFeedback',
        'DATA',
        'getFeedback',
        'getFeedbacks',
        'reset',
        'setFeedback',
        'setProgressValue',
    }

    DATA = {}

    _name_ = str()
    _doc_ = str()

    # __metaclass__ = ProcessMeta

    def __new__(cls, *args, **kwargs):
        """Generate a new class instance and setup its default attributes.
        
        The base class `Process` can't be instanciated because it is an abstract class made to be inherited
        and overrided by User Processes.
        """

        # Check if class to instanciate is Process. If True, raise an error because class is abstract.
        if cls is Process:
            raise NotImplementedError('Can not instantiate abstract class')

        # Create the instance
        instance = super(Process, cls).__new__(cls, *args, **kwargs)
        instance.__initArgs = args
        instance.__initKwargs = kwargs

        # Instance internal data (Must not be altered by user)
        instance.__threads = {}
        for memberName, member in inspect.getmembers(cls):
            if isinstance(member, Thread):
                processThread = ProcessThread(member)
                instance.__threads[memberName] = processThread
                instance.__dict__[memberName] = processThread

        instance.__feedbacks = {}
        instance.__progressbar = None
        instance.__setProgressValue = None

        instance.__stats = {}

        # Sunder instance attribute (Can be overrided user to custom the process)
        instance._name_ = cls._name_ or cls.__name__
        instance._doc_ = cls._doc_ or cls.__doc__

        # Public instance attribute (To be used by user to manage process data)
        instance.toCheck = []
        instance.toFix = []
        instance.isChecked = False

        # Sunder instance attribute (To be used by user to custom the process)
        instance._docFormat_ = {}  # The keys/values pair in this dict are retrieved to format the doc. To be used in __init__.

        return instance

    def __repr__(self):
        return '<Process {0} at {1}>'.format(self._name_, hex(id(self)))

    @property
    def data(self):
        return self.DATA

    @property
    def feedback(self):
        return self.__feedback

    @property
    def threads(self):
        return self.__threads
    
    def check(self, *args, **kwargs):
        raise NotImplementedError
        
    def fix(self, *args, **kwargs):
        raise NotImplementedError

    def tool(self, *args, **kwargs):
        raise NotImplementedError

    def setProgressValue(self, value, text=None):
        """Set the progress value of the process progressBar if exist.
        
        Parameters
        -----------
        value: numbres.Number
            The value to set the progress to.
        text: str or None
            Text to display in the progressBar, if None, the Default is used.
        """

        if self.__progressbar is None:
            return

        assert isinstance(value, numbers.Number), 'Argument `value` is not numeric'
        
        self.__progressbar.setValue(float(value))
        
        if text and text != self.__progressbar.text():
            self.__progressbar.setFormat(AtConstants.PROGRESSBAR_FORMAT.format(text))

    def reset(self):
        self._resetThreads()
        self._clearFeedback()

    def _resetThreads(self):
        for thread in self.__threads.values():
            thread.reset()

    def _clearFeedback(self):
        """Clear all feedback for this process"""
        self.__feedbacks.clear()

    def getFeedbacks(self):
        return self.__feedbacks

    def getFeedback(self, thread):
        return self.__feedbacks.get(thread, None)

    def addFeedback(self, thread, toDisplay, toSelect, selectMethod=None):
        feedback = self.getFeedback(thread)
        if feedback is None:
            self.setFeedback(thread, (toDisplay,), (toSelect,), selectMethod=selectMethod)
            return

        feedback.append(toDisplay, toSelect)

    def setFeedback(self, thread, toDisplay, toSelect, selectMethod=None):
        self.__feedbacks[thread] = Feedback(thread, toDisplay, toSelect, selectMethod=selectMethod)


# Automatic Decorator
def automatic(cls):
    """ Utility decorator to automate a process behavior.

    It allow to reset the process attributes (toCheck, toFix, data), clear the feedback etc...
    This decorator is meant to take care of redondant manipulation within a process but to keep all
    control on the code behaviour you should better manage your data by yourself.

    Parameters
    ----------
    cls: ClassType
        A class object to Wrap and make automatic.
    """    

    # Get overriden methods from the class to decorate, it's needed to redefinned the methods.
    overriddenMethods = AtUtils.getOverriddedMethods(cls, Process)

    check_ = overriddenMethods.get(AtConstants.CHECK, None)
    if check_ is not None:
        def check(self, *args, **kwargs):

            self.reset()

            self.toCheck = type(self.toCheck)()
            self.toFix = type(self.toFix)()

            result = check_(self, *args, **kwargs)

            self.isChecked = True

            return result

        setattr(cls, AtConstants.CHECK, check)  # Replace the check method in the process

    fix_ = overriddenMethods.get(AtConstants.FIX, None)
    if fix_ is not None:
        def fix(self, *args, **kwargs):

            result = fix_(self, *args, **kwargs)

            self.isChecked = False

            return result

        setattr(cls, AtConstants.FIX, fix)  # Replace the fix method in the process

    tool_ = overriddenMethods.get(AtConstants.TOOL, None)
    if tool_ is not None:
        def tool(self, *args, **kwargs):

            result = tool_(self, *args, **kwargs)

            return result

        setattr(cls, AtConstants.TOOL, tool)  # Replace the tool method in the process

    return cls


#TODO: Think about an implementation of a data feature (Share data between checks.)
class Data(object):

    def __init__(self):
        pass


class Register(object):
    """Register class that contain and manage all blueprints for all available environments.

    At initialization the register will get all data it found and store them. It will also give easy accessible data
    to work with like contexts and software.
    """

    def __init__(self, verbose=False):
        """Get the software and setup data.

        Parameters
        -----------
        verbose: bool
            Define if the function should log informations about its process. (default: False)
        """

        self.verbose = verbose
        
        self._software = AtUtils.getSoftware()

        self._data = {}
        self._packages = {}
        self._contexts = []

        self._blueprints = []

        self._context = None
        self._env = None

        self._setup()

    def __repr__(self):
        """Return the representation of the Register"""

        return "<{0} {1} - context: {2}, env: {3}>".format(
            self.__class__.__name__,
            self._software.capitalize(),
            self._context,
            self._env,
        )

    def __bool__(self):
        return bool(self._blueprints)

    __nonzero__ = __bool__

    def __eq__(self, other):
        """Allow to use '==' for logical comparison.

        This will first check if the compared object is also a Register, then it will compare all the internal data
        except the blueprints instances.

        Parameters
        ----------
        other: object
            Object to compare to this instance, should be another Register

        Notes
        -----
        Will compare:
            - software
            - contexts
            - blueprints.keys()  # (index of blueprints)
            - context  # (Current targeted context)
            - env  # (Current targeted env)
        """

        if not isinstance(other, Register):
            return False

        return all((
            self._software == other._software,
            self._contexts == other._contexts,
            self._blueprints == other._blueprints,
            self._context == other._context,
            self._env == other._env
        ))

    @property
    def data(self):
        """Get the Register internal data"""
        return self._data

    @property
    def software(self):
        """Get the Register software"""
        return self._software

    @property
    def blueprints(self):
        """Get all Register blueprints"""
        return self._blueprints

    @property
    def contexts(self):
        """Get all Register contexts"""
        return self._contexts

    @property
    def context(self):
        """Get the current context the register are pointing on"""
        return self._context

    @property
    def env(self):
        """Get the current env the register are pointing on"""
        return self._env

    def reload(self):
        """Reload data for the register instance.
        
        When this method is called it will clean data and recreate them.

        Parameters
        -----------
        verbose: bool
            Define if the function should log informations about its process. (default: False)
        """

        self._data = {}

        self._setup()

    def _setup(self):
        """Setup data for the register instance.
        
        Setup the register internal data from packages.
        The data contain all informations needed to make the tool work like each contexts, envs, blueprints and processes.
        Here only the contexts and envs are retrieved. To get blueprints, getBlueprints should be called.

        Parameters
        -----------
        verbose: bool
            Define if the function should log informations about its process. (default: False)
        """

        self._packages = packages = AtUtils.getPackages()

        for context, packageData in packages.items():
            envs = AtUtils.getEnvs(packageData['import'], software=self._software)
            
            self._data[context] = packageData
            self._data[context]['envs'] = envs

        self._contexts = packages.keys()
    
    def getEnvs(self, context):
        """Return envs stored in the given context.
        
        This will return list of envs from the given context. Especially useful to feed a widget.

        Parameters
        -----------
        context: str
            Context from which return stored envs.
        
        Returns
        -------
        list
            List of envs for the given context.
        """

        # First, get the context in data
        contextData = self._data.get(context, None)
        if contextData is None:
            return []

        # Then, get the env in the precedently queried context dict.
        envData = contextData.get('envs', None)
        if envData is None:
            return []

        return envData.keys()

    def getBlueprints(self, context, env, forceReload=False):
        """Get the blueprint object for the given context and env.
        
        Try to retrieve the blueprints for the specified env in the specified context. If there is already a blueprints,
        don't re-instanciate them if forceReload is `False`.

        Parameters
        ----------
        context: str
            Context from which retrieve the blueprint in the given env.
        env: str
            Env from which get the blueprint object.
        forceReload: bool
            Define if the function should reload its blueprints or not.

        Returns
        -------
        dict
            Dict containing all blueprint objects for the given context env.
        """

        assert context in self._contexts, '"{0}" Are not registered yet in this Register'.format(context)

        self._blueprints = []
        self._context = context

        # Get the dict for the specified context in self._data
        contextData = self._data.get(context, None)
        if contextData is None:
            return {}

        # Get the dict for all envs in self._data[context]
        envsData = contextData.get('envs', None)
        if envsData is None:
            return {}

        # Get the dict for the specified env in self._data[context]['envs']
        envData = envsData.get(env, None)
        if envData is None:
            return {}
        self._env = env

        # Get the blueprint in self._data[context]['envs'][env]. If one is found, return it.  #TODO: It seems there is an error
        blueprints = envData.get('blueprints', None)
        if blueprints is not None and not forceReload: # If not forceReload, return the existing blueprints. #FIXME: self._blueprints is empty outside dev
            return blueprints['objects']

        # Get the env module to retrieve the blueprint from.
        envModule = envData.get('module', None)
        if envModule is None:
            
            # Get the string path to the env package in self._data[context]['envs'][env]['import']
            envStr = envData.get('import', None)
            if envStr is None:
                return {}

            # Load the env module from the string path stored.
            envModule = AtUtils.importFromStr('{}.{}'.format(envStr, envData), verbose=self.verbose)
            if envModule is None:
                return {}
            envData['module'] = envModule

        # If force reload are enabled, this will reload the env module.
        if forceReload:
            AtUtils.reloadModule(envModule)

        # Try to access the `blueprints` variable in the env module
        header = getattr(envModule, 'header', ())
        blueprints = getattr(envModule, 'register', {})
        ID.flush()

        # Generate a blueprint object for each process retrieved in the `blueprint` variable of the env module.
        self._blueprints = blueprintObjects = []
        for id_ in header:
            blueprintObjects.append(Blueprint(blueprint=blueprints[id_], verbose=self.verbose))
        
        # Default resolve for blueprints if available in batch, call the `resolveLinks` method from blueprints to change the targets functions.
        batchLinkResolveBlueprints = [blueprintObject if blueprintObject._inBatch else None for blueprintObject in blueprintObjects]
        for blueprint in blueprintObjects:
            blueprint.resolveLinks(batchLinkResolveBlueprints, check=Link.CHECK, fix=Link.FIX, tool=Link.TOOL)

        # Finally store blueprints in the env dict in data.
        envData['blueprints'] = {
                'data': blueprints,
                'objects': blueprintObjects,
        }

        return self._blueprints

    def reloadBlueprintsModules(self):
        """Reload the Blueprints's source modules to reload the Processes in it
        
        Should better be called in dev mode to simplify devellopment and test of a new Process.

        Returns
        -------
        list(module, ...)
            Lis of all reloaded modules.
        """

        modules = list(set((blueprint._module for blueprint in self._blueprints)))
        for module in modules:
            AtUtils.reloadModule(module)

        return modules

    def getData(self, data):
        """Get a specific data in the register current context and env.

        Parameters
        ----------
        data: str
            The key of the data to get in the register at [self._context]['envs'][self._env]

        Returns
        -------
        type or NoneType
            Data queried if exist, else NoneType.
        """

        if not self._context or not self._env:
            return None

        return self._data[self._context]['envs'][self._env].get(data, None)

    def setData(self, key, data):
        """Set the current data at the given key of the register's current env dict.

        Parameters
        ----------
        key: type (immutable)
            The key for which to add the data in the register current context and env dict.
        data: type
            The data to store in the register's current env dict of the current context.

        Returns
        -------
        Register
            Return the instance of the object to make object fluent.
        """

        self._data[self._context]['envs'][self._env][key] = data

        return self

    def setVerbose(self, value):
        """Set the Verbose state.

        Parameters
        ----------
        value: bool
            True or False to enable or disable the verbose

        Returns
        -------
        Register
            Return the instance of the object to make object fluent.
        """

        self.verbose = bool(value)

        return self

    def getContextIcon(self, context):
        """Get the icon for the given context

        Returns
        -------
        str
            Return the icon of the queried context.
        """

        return self._packages.get(context, {}).get('icon', None)

    def getEnvIcon(self, context, env):
        """Get the icon for the given env of the given context

        Returns
        -------
        str
            Return the icon of the queried env of the given context.
        """

        return self._data[context]['envs'][env].get('icon', None)


class Blueprint(object):
    """This object will manage a single process instance to be used through an ui.

    The blueprint will init all informations it need to wrap a process like the methods that have been overrided, 
    if it can run a check, a fix, if it has a ui, its name, docstring and a lot more.
    """

    def __init__(self, blueprint, verbose=False):
        """Get the software and setup data.

        Parameters
        -----------
        blueprint: dict
            Dict containing the process string and the object (optional).
        verbose: bool
            Define if the function should log informations about its process. (default: False)
        """

        self.verbose = verbose

        self._blueprint = blueprint
        self._processStrPath = blueprint.get('process', None)
        self.category = blueprint.get('category', 'Other')
        self._parameters = self._blueprint.get('parameters', {})

        initArgs, initKwargs = self.getArguments('__init__')
        self._module, _process = AtUtils.importProcessPath(self._processStrPath)
        self._process = _process(*initArgs, **initKwargs)
        self._links = {AtConstants.CHECK: [], AtConstants.FIX: [], AtConstants.TOOL: []}

        self._name = AtUtils.camelCaseSplit(self._process._name_)
        self._docstring = self.createDocstring()

        self._check = None
        self._fix = None
        self._tool = None

        self._isEnabled = True

        self._isCheckable = False
        self._isFixable = False
        self._hasTool = False

        self._inUi = True
        self._inBatch = True

        self._isNonBlocking = False

        # setupCore will automatically retrieve the method needed to execute the process. 
        # And also the base variable necessary to define if theses methods are available.
        self.setupCore()
        self.setupTags()
        self.overrideLevels()

    def __repr__(self):
        """Return the representation of the object."""
        return "<{0} '{1}' object at {2}'>".format(self.__class__.__name__, self._process.__class__.__name__, hex(id(self)))

    @property
    def name(self):
        """Get the Blueprint's name"""
        return self._name

    @property
    def docstring(self):
        """Get the Blueprint's docstring"""
        return self._docstring

    @property
    def isEnabled(self):
        """Get the Blueprint's enabled state"""
        return self._isEnabled
    
    @property
    def isCheckable(self):
        """Get the Blueprint's checkable state"""
        return self._isCheckable

    @property
    def isFixiable(self):
        """Get the Blueprint's fixable state"""
        return self._isFixiable

    @property
    def hasTool(self):
        """Get if the Blueprint's have a tool"""
        return self._hasTool

    @property
    def inUi(self):
        """Get if the Blueprint should be run in ui"""
        return self._inUi
    
    @property
    def inBatch(self):
        """Get if the Blueprint should be run in batch"""
        return self._inBatch

    @property
    def isNonBlocking(self):
        """Get the Blueprint's non blocking state"""
        return self._isNonBlocking

    def getParameter(parameter, default=None):
        return self._parameters.get(parameter, default)

    def getLowestFailStatus(self):
        return next(iter(sorted((thread._failStatus for thread in self._threads.values()), key=lambda x: x._priority)), None)

    def getLowestSuccessStatus(self):
        return next(iter(sorted((thread._successStatus for thread in self._threads.values()), key=lambda x: x._priority)), None)
        
    def check(self, links=True):
        """This is a wrapper for the process check that will automatically execute it with the right parameters.

        Parameters
        ----------
        links: bool
            Should the wrapper launch the connected links or not.

        Returns
        -------
        type
            The check feedback.
        bool
            True if the check have any feedback, False otherwise.
        """

        if self._check is None:
            return None, None
        
        args, kwargs = self.getArguments(AtConstants.CHECK)
        returnValue = self._check(*args, **kwargs)  #TODO: Not used !!

        if links:
            self.runLinks(AtConstants.CHECK)
        
        return self._filterFeedbacks()

    def fix(self, links=True):
        """This is a wrapper for the process fix that will automatically execute it with the right parameters.
        
        Parameters
        ----------
        links: bool
            Should the wrapper launch the connected links or not.

        Returns
        -------
        type
            The value returned by the fix.
        """

        if self._fix is None:
            return None, None

        args, kwargs = self.getArguments(AtConstants.FIX)
        returnValue = self._fix(*args, **kwargs)

        if links:
            self.runLinks(AtConstants.FIX)

        return self._filterFeedbacks()

    def tool(self, links=True):
        """This is a wrapper for the process tool that will automatically execute it with the right parameters.

        Parameters
        ----------
        links: bool
            Should the wrapper launch the connected links or not.

        Returns
        -------
        type
            The value returned by the tool method.
        """

        if self._tool is None:
            return

        args, kwargs = self.getArguments(AtConstants.TOOL)
        result = self._tool(*args, **kwargs)

        if links:
            self.runLinks(AtConstants.TOOL)

        return result

    def runLinks(self, which):

        links = self._links[which]

        for link in links:
            link()

    def getArguments(self, method):
        """Retrieve arguments for the given method of the process.
        
        Parameters
        ----------
        method: classmethod
            The method for which retrieve the arguments and keyword arguments.

        Notes
        -----
        This method will not raise any error, if no argument is found, return a tuple containing empty
        list and empty dict.

        Returns
        -------
        tuple
            Tuple containing a list of args and a dict of kwargs
            => tuple(list, dict)
        """

        arguments = self._blueprint.get('arguments', None)
        if arguments is None:
            return ([], {})

        arguments = arguments.get(method, None)
        if arguments is None:
            return ([], {})

        return arguments

    def setupCore(self):
        """Setup all data for the wrapping method (check, fix, tool...) and bool to know if isCheckable, isFixable, 
        hasTool...

        Retrieve all overridden methods and set the instance attributes with the retrieved data.
        """
        
        overriddenMethods = AtUtils.getOverriddedMethods(self._process.__class__, Process)

        if overriddenMethods.get(AtConstants.CHECK, False):
            self._isCheckable = True
            self._check = self._process.check

        if overriddenMethods.get(AtConstants.FIX, False):
            self._isFixable = True
            self._fix = self._process.fix

        if overriddenMethods.get(AtConstants.TOOL, False):
            self._hasTool = True
            self._tool = self._process.tool

    def setupTags(self):
        """Setup the tags used by this process

        This method will setup the tags from the Tags given in the env module to affect the process behaviour.
        """

        tags = self._blueprint.get('tags', None)
        if tags is None:
            return

        if tags & Tag.DISABLED:
            self._isEnabled = False

        if tags & Tag.NO_CHECK:
            self._isCheckable = False

        if tags & Tag.NO_FIX:
            self._isFixable = False

        if tags & Tag.NO_TOOL:
            self._hasTool = False

        if tags & Tag.NON_BLOCKING:
            self._isNonBlocking = True

        if tags & Tag.NO_BATCH:
            self._inBatch = False

        if tags & Tag.NO_UI:
            self._inUi = False

    def resolveLinks(self, linkedObjects, check=AtConstants.CHECK, fix=AtConstants.FIX, tool=AtConstants.TOOL):
        """Resolve the links between the given objects and the current Blueprint's Process.

        This need to be called with an ordered list of Objects (Blueprint or custom object) with None for blueprints to skip.
        (e.g. to skip those that should not be linked because they dont have to be run in batch or ui.)

        Parameters
        ----------
        linkedObjects: list(object, ...)
            List of all objects used to resolve the current Blueprint links. Objects to skip have to be replace with `None`.
        check: str
            Name of the method to use as check link on the given objects.
        fix: str
            Name of the method to use as fix link on the given objects.
        tool: str
            Name of the method to use as tool link on the given objects.
        """

        self._links = {AtConstants.CHECK: [], AtConstants.FIX: [], AtConstants.TOOL: []}

        if not linkedObjects:
            return

        links = self._blueprint.get('links', None)
        if links is None:
            return

        assert all([hasattr(link, '__iter__') for link in links]), 'Links should be of type tuple(int, str, str)'
        for link in links:
            index, _driver, _driven = link
            if linkedObjects[index] is None:
                continue

            driven = _driven
            driven = check if _driven == Link.CHECK else driven
            driven = fix if _driven == Link.FIX else driven
            driven = tool if _driven == Link.TOOL else driven

            self._links[_driver].append(getattr(linkedObjects[index], driven))

    def overrideLevels(self):
        """Override the arguments level of the Process Blueprint from the data in the env module."""

        statusOverrides = self._blueprint.get('statusOverrides', None)
        if statusOverrides is None:
            return

        for threadName, overridesDict in statusOverrides.iteritems():
            if not hasattr(self._process, threadName):
                raise RuntimeError('Process {0} have not thread named {1}.'.format(self._process._name_, threadName))
            thread = getattr(self._process, threadName)
            
            # Get the fail overrides for the current name
            status = overridesDict.get(Status.FailStatus, None)
            if status is not None:
                if not isinstance(status, Status.FailStatus):
                    raise RuntimeError('Fail feedback status override for {0} "{1}" must be an instance or subclass of {2}'.format(
                        self._process._name_,
                        threadName,
                        Status.FailStatus
                    ))
                thread._failStatus = status
            
            # Get the success overrides for the current name
            status = overridesDict.get(Status.SuccessStatus, None)
            if status is not None:
                if not isinstance(status, Status.SuccessStatus):
                    raise RuntimeError('Success feedback status override for {0} "{1}" must be an instance or subclass of {2}'.format(
                        self._process._name_,
                        threadName,
                        Status.SuccessStatus
                    ))
                thread._successStatus = status

    def setProgressbar(self, progressbar):
        """ Called in the ui this method allow to give access to the progress bar for the user

        Parameters
        ----------
        progressbar: QtWidgets.QProgressBar
            QProgressBar object to connect to the process to display check and fix progression.
        """

        self._process._progressbar = progressbar

    def createDocstring(self):
        """Generate the Blueprint doc from Process docstring and data in the `_docFormat_` variable.

        Returns
        -------
        str
            Return the formatted docstring to be more readable and also display the path of the process.
        """

        docstring = self._process._doc_ or AtConstants.NO_DOCUMENTATION_AVAILABLE
        docstring += '\n {0} '.format(self._processStrPath)

        docFormat = {}
        for match in re.finditer(r'\{(\w+)\}', docstring):
            matchStr = match.group(1)
            docFormat[matchStr] = self._process._docFormat_.get(matchStr, '')

        return docstring.format(**docFormat)

    # DEPRECATED 1.0.0
    # def _filterResult(self, result):
    #     """ Filter the data ouputed by a process to keep only these that is not empty.

    #     Parameters
    #     ----------
    #     result: tuple
    #         Tuple containing tuple with a str for title and list of errors.
    #         > tuple(tuple(str, list, `list`, `str`), ...)

    #     Returns
    #     -------
    #     list
    #         List of feedbacks that contain at least one error to log or only a title. 
    #     """

    #     filtered_result = []
    #     for feedback in result:
    #         toDisplay = feedback['toDisplay']
    #         if not toDisplay:
    #             continue
    #         elif feedback['toDisplay'] is Ellipsis:
    #             feedback['toDisplay'] = []
    #             feedback['toSelect'] = []

    #         filtered_result.append(feedback)

    #     return filtered_result

    # def _filterFeedbacks(self):
    #     """ Filter the data outputed by a process to keep only these that is not empty.

    #     Parameters
    #     ----------
    #     result: tuple
    #         Tuple containing tuple with a str for title and list of errors.
    #         > tuple(tuple(str, list, `list`, `str`), ...)

    #     Returns
    #     -------
    #     list
    #         List of feedbacks that contain at least one error to log or only a title. 
    #     """

    #     globalFailStatus = self.getLowestFailStatus()
    #     globalSuccessStatus = self.getLowestSuccessStatus()

    #     feedbackContainer = []
    #     for thread, feedback in self._process.feedback.items():
    #         if feedback:
    #             feedbackContainer.append(feedback)
    #             if thread._failStatus._priority >= globalFailStatus._priority:
    #                 globalFailStatus = thread._failStatus
    #         else:
    #             if thread._successStatus._priority <= globalSuccessStatus._priority:
    #                 globalSuccessStatus = thread._successStatus

    #     return feedbackContainer, globalFailStatus if feedbackContainer else globalSuccessStatus

    def _filterFeedbacks(self):
        """ Filter the data outputed by a process to keep only these that is not empty.

        Parameters
        ----------
        result: tuple
            Tuple containing tuple with a str for title and list of errors.
            > tuple(tuple(str, list, `list`, `str`), ...)

        Returns
        -------
        list
            List of feedbacks that contain at least one error to log or only a title. 
        """

        # We always consider that the result should be the lowest success status.
        globalStatus = Status._DEFAULT

        feedbackContainer = []
        for processThreadName, processThread in self._process.threads.iteritems():

            # Get the feedaback, if there is no feedback for this thread it is clean.
            feedback = self._process.getFeedback(processThread)
            if feedback:
                feedbackContainer.append(feedback)

            # If there is anything in the feedback we check if we need to increase the fail status and we add the feedback in
            # the container to return it.
            # if thread._state is Status.FailStatus:
            if processThread._status._priority > globalStatus._priority:
                globalStatus = processThread._status

            # If the feedback is empty this thread was succesfull, we increase success status if the status of this thread is
            # higher to the current one retrieved.
            # elif thread._state is Status.SuccessStatus:
            #     print thread._status._name, thread._status._priority, globalSuccessStatus._priority
            #     if thread._status._priority > globalSuccessStatus._priority:
            #         globalSuccessStatus = thread._status

        return feedbackContainer, globalStatus # if feedbackContainer else globalSuccessStatus


class Tag(object):
    """Tags are modifiers used by Athena to affect the way a process could be run, through or outside a ui.
    It Allow processes to be optional, non blocking, hide their checks and more.

    Attributes
    ----------
    DISABLED: str
        Define if a process should be disabled (by default it is enable)
    NO_CHECK: str
        This tag will remove the check of a process, it will force the isCheckable to False in blueprint.
    NO_FIX: str
        This tag will remove the fix of a process, it will force the isFixable to False in blueprint.
    NO_TOOL: str
        This tag will remove the tool of a process, it will force the hasTool to False in blueprint.
    NON_BLOCKING: str
        A non blocking process will raise a non blocking error, its error is ignored.
    NO_BATCH: str
        This process will only be executed in ui.
    NO_UI: str
        This process will only be executed in batch.
    OPTIONAL: str
       This tag will set a check optional, an optional process is not checked by default and will.
    DEPENDANT: str
        A dependent process need links to be run through another process.
    """

    DISABLED        = 1

    NO_CHECK        = 2
    NO_FIX          = 4
    NO_TOOL         = 8

    NON_BLOCKING    = 16
    
    NO_BATCH        = 32
    NO_UI           = 64
    
    OPTIONAL        = NON_BLOCKING | DISABLED
    DEPENDANT       = NO_CHECK | NO_FIX | NO_TOOL


class Link(object):
    """Give access to the AtConstants to simplify the use of the links."""

    CHECK = AtConstants.CHECK
    FIX = AtConstants.FIX
    TOOL = AtConstants.TOOL


class MetaID(type):
        
    def __getattr__(cls, value):
        id_ = hex(hash(value))

        if id_ not in cls._DATA:
            setattr(cls, value, id_)
            cls._DATA[value] = id_

        return id_

    def __getattribute__(cls, value):
        
        if value in type.__dict__:
            raise ValueError('Can not create ID: `{0}`, it will override python <type> inherited attribute of same name.'.format(value))

        return type.__getattribute__(cls, value)

#TODO: six is used to ensure compatibility between python 2.x and 3.x, replace by `object, metaclass=MetaID`
class ID(six.with_metaclass(MetaID, object)):
    
    _DATA = {}

    def __new__(cls):
        raise NotImplementedError('{0} is not meant to be instanciated.'.format(cls))

    @classmethod
    def flush(cls):
        for key in cls._DATA:
            delattr(cls, key)
        
        cls._DATA.clear()


class Status(object):
    """The Status define the level of priority of a Thread Feedback as well as the state of a process.

    The process must be given an original name and a level of priority, the priority must be <0 for a fail status and 
    >0 for a success status. Other type does not use the priority.
    The color is an rgb value that will then allow to set the color in an interface.
    
    .. notes::
        The Status object can't be instanciated, instead, the __Status object will be instantiated and returned.
        The Status class already create some instances of the __Status class to define the bases Status to be Used by 
        default.
    """

    class __Status(object):
        
        _ALL_STATUS = {}

        def __new__(cls, *args, **kwargs):
            """Allow to store all new levels in the __ALL_LEVELS class variable to return singleton."""
            instance = super(cls.__class__, cls).__new__(cls)
            cls._ALL_STATUS.setdefault(instance.__class__, set()).add(instance)

            return instance
        
        def __init__(self, name, color, priority=0.0):

            self._name = name
            self._priority = priority
            self._color = color

    class FailStatus(__Status):
        
        def __init__(self, *args, **kwargs):
            super(self.__class__, self).__init__(*args, **kwargs)

    class SuccessStatus(__Status):
        
        def __init__(self, *args, **kwargs):
            super(self.__class__, self).__init__(*args, **kwargs)

    class FeedbackStatus(__Status):
        
        def __init__(self, *args, **kwargs):
            super(self.__class__, self).__init__(*args, **kwargs) 

    class BuiltInStatus(__Status):
        
        def __init__(self, *args, **kwargs):
            super(self.__class__, self).__init__(*args, **kwargs)

    _DEFAULT =  BuiltInStatus('Default', (65, 65, 65))

    # INFO =  FeedbackStatus('Info', (200, 200, 200))
    PAUSED = FeedbackStatus('Paused', (255, 186, 0))

    CORRECT = SuccessStatus('Correct', (22, 194, 15), 0.1)
    SUCCESS = SuccessStatus('Success', (0, 128, 0), 0.2)

    WARNING = FailStatus('Warning', (196, 98, 16), 1.1)
    ERROR = FailStatus('Error', (102, 0, 0), 1.2)
    CRITICAL = FailStatus('Critical', (150, 0, 0), 1.3)

    _EXCEPTION = BuiltInStatus('Exception', (110, 110, 110))

    def __new__(cls, type, *args, **kwargs):
        raise RuntimeError('Can\'t create new instance of type `{0}`.'.format(cls.__name__))

    @classmethod
    def getAllStatus(cls):
        return [status for statusTypeList in cls.__Status._ALL_STATUS.values() for status in statusTypeList]

    @classmethod
    def getAllFailStatus(cls):
        return cls.__Status._ALL_STATUS[cls.FailStatus]

    @classmethod
    def getAllSuccessStatus(cls):
        return cls.__Status._ALL_STATUS[cls.SuccessStatus]

    @classmethod
    def lowestFailStatus(cls):
        return sorted(cls.getAllFailStatus(), key=lambda x: x._priority)[0]

    @classmethod
    def highestFailStatus(cls):
        return sorted(cls.getAllFailStatus(), key=lambda x: x._priority)[-1]

    @classmethod
    def lowestSuccessStatus(cls):
        return sorted(cls.getAllSuccessStatus(), key=lambda x: x._priority)[0]

    @classmethod
    def highestSuccessStatus(cls):
        return sorted(cls.getAllSuccessStatus(), key=lambda x: x._priority)[-1]


class Feedback(object):
    """This onbject contain all the data to describe one feedback that have. been checked in a Process."""

    def __init__(self, thread, toDisplay, toSelect, selectMethod=None, help=None):
        
        if len(toDisplay) != len(toSelect):
            raise ValueError('You must have the same amount of object to select and to display')

        self._thread = thread

        self._toDisplay = list(toDisplay)
        self._toSelect = list(toSelect) or self._toDisplay

        self._selectMethod = selectMethod or AtUtils.softwareSelection

    def selectAll(self):
        self._selectMethod(self._toSelect)

    def select(self, indexes):
        self._selectMethod([self._toSelect[i] for i in indexes])

    def hasFeedback(self):
        return bool(self._toDisplay)

    def append(self, toDisplay, toSelect, selectMethod=None):
        self._toDisplay.append(toDisplay)
        self._toSelect.append(toSelect)

        if selectMethod is not None and self._selectMethod is not AtUtils.softwareSelection:
            self._selectMethod = selectMethod

    def iterItems(self):
        for item in list(zip(self._toDisplay, self._toSelect)):
            yield item

    def __bool__(self):
        return bool(self._toSelect)

    __nonzero__ = __bool__


class Thread(object):

    def __init__(self, title, failStatus=Status.ERROR, successStatus=Status.SUCCESS, documentation=None):
        if not isinstance(failStatus, Status.FailStatus):
            raise RuntimeError('`{}` is not a valid fail status.'.format(failStatus._name))
        if not isinstance(successStatus, Status.SuccessStatus):
            raise RuntimeError('`{}` is not a valid success status.'.format(successStatus._name))

        self._title = title

        self._defaultFailStatus = failStatus
        self._failStatus = failStatus

        self._defaultSuccessStatus = successStatus
        self._successStatus = successStatus

        self._documentation = documentation

    @property
    def failStatus(self):
        return self._failStatus

    @property
    def successStatus(self):
        return self._successStatus


class ProcessThread(Thread):

    def __init__(self, thread):

        super(ProcessThread, self).__init__(
            title=thread._title, 
            failStatus=thread._defaultFailStatus,
            successStatus=thread._defaultSuccessStatus,
            documentation=thread._documentation
        )

        self._thread = thread
        self._enabled = True

        self._state = Status.SuccessStatus
        self._status = self._successStatus

    @property
    def thread(self):
        return self._thread
    
    @property
    def state(self):
        return self._state

    def reset(self):
        self._state = Status.SuccessStatus
        self._status = self._successStatus

    def enable(self):
        self._enabled = True

    def disable(self):
        self._enabled = False

    def setFail(self, overrideStatus=None):
        if overrideStatus is not None:
            if isinstance(overrideStatus, Status.FailStatus):
                self._status = overrideStatus
            else:
                raise TypeError('Fail Status can only be an instance or subtype of `{}`.'.format(type(Status.FailStatus)))
        else:
            self._status = self._failStatus

        self._state = Status.FailStatus

    def setSuccess(self, overrideStatus=None):
        if overrideStatus is not None:
            if isinstance(overrideStatus, Status.SuccessStatus):
                self._status = overrideStatus
            else:
                raise TypeError('Success Status can only be an instance or subtype of `{}`.'.format(type(Status.SuccessStatus)))
        else:
            self._status = self._successStatus

        self._state = Status.SuccessStatus


class Event(object):

    def __init__(self, name):
        super(Event, self).__init__()
        self.name = name
        self.callbacks = []

    def __call__(self):
        for callback in self.callbacks:
            callback()

    def register(self, callback):
        if not callable(callback):
            LOGGER.warning(
                'Event "{0}" failed to register callback: Object "{1}" is not callable'.format(self.name, callback)
            )
            return False

        self.callbacks.append(callback)
        return True

    def unregister(self, callback):
        pass


class Profiler(object):

    def __init__(self, callable):
        self._profiler = cProfile.Profile()

        self._callable = callable

        self._stats = ''

    def __enter__(self):
        self._profiler.runcall(self._callable)

        # Create a 
        with open('stats.stat', 'w') as statStream:
            stats = pstats.Stats(profile, stream=statStream)
            stats.print_stats()
        
        with open('stats.stat', 'r') as statStream:
            self._stats = statStream.read()

# sys.path.append('C:\Python27\Lib\site-packages')

# def merge_env(env_pck):

#     to_merge = []

#     for first_env in env_pck:
#         for second_env in env_pck:
#             if first_env == second_env:
#                 continue
#             if first_env[-1][0] == second_env[-1][0]:
#                 index = None
#                 for i in range(len(to_merge)):
#                     if to_merge[i] != first_env[-1][0]:
#                         continue
#                     index = i
#                 if index is None:
#                     to_merge.append((first_env[-1][0], []))
#                     index = -1
#                 to_merge[index][-1].append(second_env[-1][-1])

#     return to_merge




""" #TODO: This snippet of code is now out of date
def start(env, register, verbose=False):

    processes = load_moduleStr('{}.{}'.format(env, register))
    print Register.extract()

    for process in []:

        # separate module hierarchy from class to instance (check)
        moduleStr, class_str = process[0].rsplit('.', 1)

        module = load_moduleStr(moduleStr, verbose=verbose)  #module etant une instance comme cmds le serait. il devrait avoir une plus grande portee.

        if module is None:
            raise RuntimeError('Module {0} can not be found'.format(moduleStr))

        # get the process class <class 'gpdev.tools.Athena.testCheck.TestForSanityCheck'>
        processClass = getattr(module, class_str, None) #TODO Enhance this process
        if processClass is None:
            raise RuntimeError('Process class {0} can not be found in module {1}'.format(class_str, moduleStr))

        if processClass: #create an instance.
            __process = processClass()  # instance de la class <gpdev.tools.Athena.testCheck.TestForSanityCheck object at 0x000001A4C70B4A90>

        if not __process:
            raise RuntimeError('Unable to instance ' + processClass) #custom erreurs
        
        # get list of methods that have been overrided (implemented.)
        overrided_method = AtUtils.getOverriddedMethods(processClass, Process)

        print overrided_method

        # if '__init__' in overrided_method:
        #     print '__init__ for ' + str(processClass)
        #     __process.__init__()

        # if AtConstants.CHECK in overrided_method:
        #     print 'check for ' + str(processClass)
        #     __process.check()

        # if AtConstants.FIX in overrided_method:
        #     print 'fix for ' + str(processClass)
        #     __process.fix()


# This function is the entry point to load all environmnet
def main(envs=None, verbose=False):

    if not envs:
        envs = AtUtils.get_envs()  # Get all already imported envs
        if verbose: print('{} envs have been succesfully retrieved ({})'.format(len(envs), ', '.join(envs)))

    # Keys will be the envs resolved path and the values will be associated env_pck
    env_pck = {}
    for env in envs:
        env_pck[env] = AtUtils.rez_env(env)

    if not env_pck:
        AtConstants.LOGGER.info('No envs available') 
        return

    process_registers = env_pck.get(env[0], None)
    if process_registers is None:
        raise ImportError('No register {0} have been resolved, you should import them'.format(register))

    process_importer = process_registers.get(register, None)
    if process_importer is None:
        raise ImportError('No register {0} found in env {1}'.format(register, env))  # An env is a package containing register that is like modules.

    start(env, register)

"""
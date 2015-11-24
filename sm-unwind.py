import gdb

_have_unwinder = True
try:
    from gdb.unwinder import Unwinder
except ImportError:
    _have_unwinder = False

def debug(something):
    print(something)
    pass

# FIXME should come from a cache
FRAMETYPE_MASK = (1 << gdb.parse_and_eval('js::jit::FRAMETYPE_BITS')) - 1
FRAMESIZE_SHIFT = gdb.parse_and_eval('js::jit::FRAMESIZE_SHIFT')

# Must be in sync with JitFrames.cpp:SizeOfFramePrefix.
# Maps frametype enum base names to corresponding class.
SizeOfFramePrefix = {
    'JitFrame_Entry': 'EntryFrameLayout',

    'JitFrame_BaselineJS': 'JitFrameLayout',
    'JitFrame_IonJS': 'JitFrameLayout',
    'JitFrame_Bailout': 'JitFrameLayout',
    'JitFrame_Unwound_BaselineJS': 'JitFrameLayout',
    'JitFrame_Unwound_IonJS': 'JitFrameLayout',

    'JitFrame_BaselineStub': 'BaselineStubFrameLayout',
    'JitFrame_Unwound_BaselineStub': 'BaselineStubFrameLayout',
    
    'JitFrame_IonStub': 'JitStubFrameLayout',
    'JitFrame_Unwound_IonStub': 'JitStubFrameLayout',

    'JitFrame_Rectifier': 'RectifierFrameLayout',

    'JitFrame_Unwound_Rectifier': 'IonUnwoundRectifierFrameLayout',

    'JitFrame_Exit': 'ExitFrameLayout',
    'JitFrame_LazyLink': 'ExitFrameLayout',

    'JitFrame_IonAccessorIC': 'IonAccessorICFrameLayout',
    'JitFrame_Unwound_IonAccessorIC': 'IonAccessorICFrameLayout',
}

# Compute map, indexed by a JitFrame value (an integer), whose
# values are size of corresponding frame classes.
def compute_frame_size_map():
    global SizeOfFramePrefix
    t = gdb.lookup_type('enum js::jit::FrameType')
    result = {}
    for field in t.fields():
        # Strip off "js::jit::".
        name = field.name[9:]
        class_type = gdb.lookup_type('js::jit::' + SizeOfFramePrefix[name])
        result[int(field.enumval)] = class_type.sizeof
    return result

# FIXME this should be on a cache somewhere
frame_size_map = compute_frame_size_map()

# FIXME another cache candidate.
per_tls_data = gdb.lookup_global_symbol('js::TlsPerThreadData')

class SpiderMonkeyFrameId(object):
    def __init__(self, sp, pc):
        self.sp = sp
        self.pc = pc

class UnwinderState(object):
    SP_REGISTER = 'rsp'
    PC_REGISTER = 'rip'

    def __init__(self):
        debug("@@ new UnwinderState")
        self.expected_sp = None
        self.activation = None
        self.jittop = None
        self.thread = gdb.selected_thread()
        # FIXME cache
        commonFrameLayout = gdb.lookup_type('js::jit::CommonFrameLayout')
        self.typeCommonFrameLayoutPointer = commonFrameLayout.pointer()

    def check(self):
        return gdb.selected_thread() is self.thread

    def get_tls_per_thread_data(self):
        global per_tls_data
        return per_tls_data.value()['mValue']

    def unpack_descriptor(self, common):
        value = common['descriptor_']
        size = value >> FRAMESIZE_SHIFT
        frame_type = value & FRAMETYPE_MASK
        return (size, frame_type)

    def sizeof_frame_type(self, frame_type):
        global frame_size_map
        return frame_size_map[int(frame_type)]

    def unwind_ordinary(self, pc, sp, pending_frame):
        debug("@@ unwind_ordinary")
        common = sp.cast(self.typeCommonFrameLayoutPointer)
        debug("@@ common = %s" % str(common.dereference()))
        new_pc = common['returnAddress_']
        debug("@@ new_pc = 0x%x" % new_pc)
        (size, frame_type) = self.unpack_descriptor(common)
        debug("@@ size, frame_type = %s" % str((int(size), int(frame_type))))
        self.expected_sp = sp + size + self.sizeof_frame_type(frame_type)
        debug("@@ expected_sp = 0x%x" % self.expected_sp)
        frame_id = SpiderMonkeyFrameId(self.expected_sp, pc)
        # FIXME - here is where we'd register the frame
        # info for dissection in the frame filter
        # FIXME it would be great to unwind any other registers here.
        return pending_frame.create_unwind_info(frame_id)
        
    def unwind_exit_frame(self, pc, pending_frame):
        debug("@@ unwind_exit_frame")
        if self.activation == 0:
            # Reached the end of the list.
            self.expected_sp = None
            return None
        if self.activation is None:
            ptd = self.get_tls_per_thread_data()
            self.activation = ptd['runtime_']['jitActivation']
            self.jittop = ptd['runtime_']['jitTop']
        else:
            self.jittop = self.activation['prevJitTop_']
            self.activation = self.activation['prevJitActivation_']
        debug("@@ jittop = 0x%x" % self.jittop)

        # Now we can just fall into the ordinary case.
        return self.unwind_ordinary(pc, self.jittop, pending_frame)

    def unwind(self, pending_frame):
        pc = pending_frame.read_register(self.PC_REGISTER)

        # If some shared library claims this address, bail.  GDB
        # defers to our unwinder by default, but we don't really want
        # that kind of power.
        if gdb.solib_name(int(pc)) is not None:
            debug("@@ early exit: %s" % gdb.solib_name(int(pc)))
            return None

        sp = pending_frame.read_register(self.SP_REGISTER)
        if sp == self.expected_sp:
            return self.unwind_ordinary(pc, sp, pending_frame)
        # Maybe we've found an exit frame.  FIXME I currently don't
        # know how to identify these precisely, so we'll just hope for
        # the time being.
        return self.unwind_exit_frame(pc, pending_frame)

unwinder_state = None

class SpiderMonkeyUnwinder(Unwinder):
    def __init__(self):
        super(SpiderMonkeyUnwinder, self).__init__("SpiderMonkey")

    def __call__(self, pending_frame):
        global unwinder_state
        if unwinder_state is None or not unwinder_state.check():
            unwinder_state = UnwinderState()
        return unwinder_state.unwind(pending_frame)

def invalidate_unwinder_state(*args, **kwargs):
    unwinder_state = None

# FIXME - should register with the objfile (or wherever SpiderMonkey
# pretty-printers go)
if _have_unwinder:
    # We need to invalidate the unwinder state whenever the inferior
    # starts executing.  This avoids having a stale cache.
    gdb.events.cont.connect(invalidate_unwinder_state)
    gdb.unwinder.register_unwinder(None, SpiderMonkeyUnwinder())

import gdb
import GdbJitReader
import struct

def get_pack_fmt(buffer)
    if len(buffer) == 4:
        fmt = 'I'
    else:
        # len == 8
        fmt = 'L'
    return fmt

def unpack_addr(reg_buffer):
    return struct.unpack_from(get_pack_fmt(reg_buffer), reg_buffer)

FRAMETYPE_MASK = (1 << gdb.parse_and_eval('js::jit::FRAMETYPE_BITS')) - 1
FRAMESIZE_SHIFT = gdb.parse_and_eval('js::jit::FRAMESIZE_SHIFT')

CalleeTokenTagMask = 3  # also hard-coded in JS
CalleeTokenMask = gdb.parse_and_eval('js::jit::CalleeTokenMask')

# Must be in sync with JitFrames.cpp:SizeOfFramePrefix.
# Maps frametype enum values to corresponding class.
SizeOfFramePrefix = {
    'JitFrame_Entry': 'EntryFrameLayout',

    'JitFrame_BaselineJS': 'JitFrameLayout',
    'JitFrame_IonJS': 'JitFrameLayout',
    'JitFrame_Bailout': 'JitFrameLayout',
    'JitFrame_Unwound_BaselineJS': 'JitFrameLayout',
    'JitFrame_Unwound_IonJS': 'JitFrameLayout',

    'JitFrame_BaselineStub': 'BaselineStubFrameLayout',
    'JitFrame_Unwound_BaselineStub': 'BaselineStubFrameLayout',
    
    'JitFrame_Rectifier': 'RectifierFrameLayout',

    'JitFrame_Unwound_Rectifier': 'IonUnwoundRectifierFrameLayout',

    'JitFrame_Exit': 'ExitFrameLayout',

    'JitFrame_IonAccessorIC': 'IonAccessorICFrameLayout',
    'JitFrame_Unwound_IonAccessorIC': 'IonAccessorICFrameLayout',
}


def init_frame_type_map():
    t = gdb.lookup_type('enum js::jit::FrameType')
    result = []
    for field in t.fields():
        result[field.enumval] = field.name
    return result

# Array mapping frametype enum values to names.
FrameTypeMap = init_frame_type_map()


def callee_token_to_script(token):
    tag = long(token) & CalleeTokenTagMask
    token = long(token) & CalleeTokenMask
    # FIXME
    # return Value(token).cast(gdb.lookup_type('js::JSScript').pointer())

def unwind_ordinary(pc, callbacks):
    regs = []
    sp = callbacks.get_register(SP_REGNO)
    fmt = get_pack_fmt(sp)
    sp = struct.unpack_from(fmt, sp)
    descriptor = struct.unpack_from(fmt, callbacks.read_memory(sp, size))
    regs[PC_REGNO] = callbacks.read_memory(sp + size, size)
    args_size = descriptor >> FRAMESIZE_SHIFT
    frame_type = descriptor & FRAMETYPE_MASK
    type_size = type_sizes[frame_type]
    regs[SP_REGNO] = struct.pack(fmt, sp + args_size + type_size)
    return regs

class SpiderMonkeyUnwinder(object):
    def unwind(self, callbacks):
        # If the PC belongs in some existing shared library, it can't
        # be ours.
        pc = unpack_addr(callbacks.get_register(PC_REGNO))
        if gdb.solib_name(pc) is not None:
            return False

        if self.is_trampoline(pc):
            return self.unwind_trampoline(pc, callbacks)

        return unwind_ordinary(pc, callbacks)

    def get_frame_id(self, callbacks):
        sp = callbacks.get_register(SP_REGNO)
        fmt = get_register(sp)
        sp = struct.unpack_from(fmt, sp)
        descriptor = struct.unpack_from(fmt, callbacks.read_memory(sp, size))
        # FIXME find start of function
        pc = struct.unpack_from(fmt, callbacks.read_memory(sp + size, size))
        return (pc, sp)

class x64_info(SpiderMonkeyUnwinder):
    SP_REGNO = 7
    PC_REGNO = 16

    # FIXME define other registers here.

    def is_trampoline(self, pc):
        # FIXME - we need special handling for trampoline frames
        # this is just JitRuntime:: enterJIT_ and enterBaselineJIT_
        # Maybe this can move to the base class
        return False

    def unwind_trampoline(self, pc, callbacks):
        sp = callbacks.get_register(SP_REGNO)
        wordsize = len(sp)
        sp = unpack_addr(sp)
        regs = []
        # Must be in sync with Trampoline-x64.cpp:generateEnterJIT.
        pushed_regs = [rbp, rbx, r12, r13, r14, r15]
        for reg in pushed_regs:
            sp = sp - wordsize
            regs[reg] = callbacks.read_memory(sp, wordsize)
        return regs

# FIXME
GdbJitReader.register_jit_reader(x64_info())

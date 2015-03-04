#include <stdlib.h>
#include <gdb/jit-reader.h>
#include <assert.h>
#include <stdint.h>

/* Fear not, RMS.  */
GDB_DECLARE_GPL_COMPATIBLE_READER

/* DWARF register numbers, from the ABI.  */
/* FIXME just amd64 for now.  */
static const int AMD64_RSP_REGNUM = 7;
static const int AMD64_RIP_REGNUM = 16;

static const int TARGET_WORD_SIZE = 8;

/* I suppose we could get these from the real debuginfo.  */
static const int SM_SIZEOF_VOIDP = 8;
static const int SM_OFFSET_RETURN_ADDRESS = 0;
static const int SM_OFFSET_DESCRIPTOR = 8;

/* This of course is just evil.  */
static const int SM_ENTRYFRAME_SIZE = 32;
/* ... */

static const uintptr_t FRAMESIZE_SHIFT = 4;
static const uintptr_t FRAMETYPE_BITS = 4;

static GDB_CORE_ADDR
bytes_to_value (int size, unsigned char *value)
{
  GDB_CORE_ADDR result = 0;

  /* This is where we'd do byte-swapping... */
  for (int i = size; i > 0; --i)
    result = result << 8 | *value++;

  return result;
}

static enum gdb_status
spidermonkey_read (struct gdb_reader_funcs *self,
		   struct gdb_symbol_callbacks *gdb,
		   void *memory, long memory_sz)
{
  /* Nothing to do here yet.  If we want more than raw unwinding, say
     function names, then we'll need to implement this.  */
  return GDB_SUCCESS;
}

static void
do_free (struct gdb_reg_value *v)
{
  free (v);
}

static struct gdb_reg_value *
allocate_register (int size, GDB_CORE_ADDR bytes)
{
  struct gdb_reg_value *result;

  result = malloc (sizeof (struct gdb_reg_value) + size - 1);
  result->size = size;
  result->defined = 1;
  result->free = do_free;

  for (int i = 0; i < size; ++i)
    {
      result->value[i] = bytes & 0xff;
      bytes >>= 8;
    }

  return result;
}

static enum gdb_status
spidermonkey_unwind (struct gdb_reader_funcs *self,
		     struct gdb_unwind_callbacks *gdb)
{
  enum gdb_status result = GDB_FAIL;
  struct gdb_reg_value *value;
  GDB_CORE_ADDR addr, descriptor;
  unsigned char data[TARGET_WORD_SIZE];

  value = gdb->reg_get (gdb, AMD64_RSP_REGNUM);
  if (!value->defined)
    goto fail;
  assert (value->size == TARGET_WORD_SIZE);
  assert (value->size <= sizeof (GDB_CORE_ADDR));

  addr = bytes_to_value (value->size, &value->value[0]);
  if (gdb->target_read (addr + SM_OFFSET_DESCRIPTOR, &data, TARGET_WORD_SIZE)
      != GDB_SUCCESS)
    goto fail;

  descriptor = bytes_to_value (TARGET_WORD_SIZE, data);
  /* Heuristic to see if this seems plausible.  */
  /* It would be great if we had a reliable method here.  */
  /* FIXME gdb should let us register this as low-priority sniffer.  */
  if ((descriptor & ((1 << FRAMETYPE_BITS) - 1)) > 12
      /* What's a maximal stack frame size?  */
      || (descriptor >> FRAMESIZE_SHIFT) > 150)
    goto fail;

  /* Compute the unwound stack pointer.  */
  gdb->reg_set (gdb, AMD64_RSP_REGNUM,
		allocate_register (TARGET_WORD_SIZE,
				   addr + (descriptor >> FRAMESIZE_SHIFT)
				   + SM_ENTRYFRAME_SIZE));

  /* Fetch the return address.  */
  if (gdb->target_read (addr + SM_OFFSET_RETURN_ADDRESS, &data, TARGET_WORD_SIZE)
      != GDB_SUCCESS)
    goto fail;
  gdb->reg_set (gdb, AMD64_RIP_REGNUM,
		allocate_register (TARGET_WORD_SIZE,
				   bytes_to_value (TARGET_WORD_SIZE, data)));

  /* FIXME - not sure where to find the other ones.  */

  result = GDB_SUCCESS;

 fail:
  value->free (value);

  return result;
}

static struct gdb_frame_id
spidermonkey_get_frame_id (struct gdb_reader_funcs *self,
			   struct gdb_unwind_callbacks *gdb)
{
  struct gdb_frame_id result = { 0, 0 };
  struct gdb_reg_value *value;

  value = gdb->reg_get (gdb, AMD64_RSP_REGNUM);
  assert (value->defined);
  assert (value->size == TARGET_WORD_SIZE);
  assert (value->size <= sizeof (GDB_CORE_ADDR));

  result.stack_address = bytes_to_value (value->size, &value->value[0]);

  /* Use the return address, since that is also unvarying and also
     easy to fetch.  */
  unsigned char data[TARGET_WORD_SIZE];
  enum gdb_status status = gdb->target_read (result.stack_address
					     + SM_OFFSET_RETURN_ADDRESS,
					     &data,
					     TARGET_WORD_SIZE);
  /* There's no way to return an error from get_frame_id, whoops.  */
  assert (status == GDB_SUCCESS);

  result.code_address = bytes_to_value (TARGET_WORD_SIZE, data);

  value->free (value);

  return result;
}

static void
spidermonkey_destroy (struct gdb_reader_funcs *self)
{
  /* Nothing to do.  */
}

static struct gdb_reader_funcs spidermonkey_funcs =
{
  GDB_READER_INTERFACE_VERSION,
  NULL,
  spidermonkey_read,
  spidermonkey_unwind,
  spidermonkey_get_frame_id,
  spidermonkey_destroy
};

struct gdb_reader_funcs *
gdb_init_reader (void)
{
  return &spidermonkey_funcs;
}

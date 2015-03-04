#include <gdb/jit-reader.h>
#include <Python.h>

/* Fear not, RMS.  */
GDB_DECLARE_GPL_COMPATIBLE_READER

static PyObject *module;
static PyObject *reader;

struct callbacks_obj
{
  PyObject_HEAD

  struct gdb_unwind_callbacks *gdb;
};

/* Python error-handing policy is here.  */
static void
handle_error (void)
{
  PyErr_Print ();
}

static PyObject *
get_register (PyObject *s, PyObject *args)
{
  struct callbacks_obj *self = (struct callbacks_obj *) s;
  int regno;
  PyObject *result;

  if (!PyArg_ParseTuple (args, "I", &regno))
    return NULL;

  struct gdb_reg_value *raw_value = self->gdb->reg_get (self->gdb, regno);
  if (raw_value->defined)
    result = PyByteArray_FromStringAndSize (&raw_value->value[0],
					    raw_value->size);
  else
    {
      result = Py_None;
      Py_INCREF (result);
    }

  raw_value->free (raw_value);
  return result;
}

static PyObject *
read_memory (PyObject *s, PyObject *args)
{
  struct callbacks_obj *self = (struct callbacks_obj *) s;
  unsigned PY_LONG_LONG addr, len;

  if (!PyArg_ParseTuple (args, "KK", &addr, &len) < 0)
    return NULL;

  void *mem = malloc (len);
  if (mem == NULL)
    {
      PyErr_SetString (PyExc_MemoryError, "couldn't allocate memory for buffer");
      return NULL;
    }

  if (self->gdb->target_read (addr, mem, len) == GDB_FAIL)
    {
      PyErr_SetString (PyExc_MemoryError, "could not read memory");
      free (mem);
      return NULL;
    }

  PyObject *result = PyByteArray_FromStringAndSize (mem, len);
  free (mem);
  return result;
}

static PyMethodDef callbacks_methods[] =
{
  { "get_register", get_register, METH_VARARGS,
    "Fetch a register value." },
  { "read_memory", read_memory, METH_VARARGS,
    "Read memory from target." },
  { NULL }
};

static PyTypeObject jit_callback_type =
{
  PyVarObject_HEAD_INIT (NULL, 0)
  "GdbJitReader.Callbacks",	  /*tp_name*/
  sizeof (struct callbacks_obj),  /*tp_basicsize*/
  0,				  /*tp_itemsize*/
  0,				  /*tp_dealloc*/
  0,				  /*tp_print*/
  0,				  /*tp_getattr*/
  0,				  /*tp_setattr*/
  0,				  /*tp_compare*/
  0,				  /*tp_repr*/
  0,				  /*tp_as_number*/
  0,				  /*tp_as_sequence*/
  0,				  /*tp_as_mapping*/
  0,				  /*tp_hash */
  0,				  /*tp_call*/
  0,				  /*tp_str*/
  0,				  /*tp_getattro*/
  0,				  /*tp_setattro*/
  0,				  /*tp_as_buffer*/
  Py_TPFLAGS_DEFAULT,		  /*tp_flags*/
  "callbacks object",		  /* tp_doc */
  0,				  /* tp_traverse */
  0,				  /* tp_clear */
  0,				  /* tp_richcompare */
  0,				  /* tp_weaklistoffset */
  0,				  /* tp_iter */
  0,				  /* tp_iternext */
  callbacks_methods,		  /* tp_methods */
  0,				  /* tp_members */
  0,				  /* tp_getset */
  0,				  /* tp_base */
  0,				  /* tp_dict */
  0,				  /* tp_descr_get */
  0,				  /* tp_descr_set */
  0,				  /* tp_dictoffset */
  0,				  /* tp_init */
  0,				  /* tp_alloc */
  0,				  /* tp_new */
};

static PyObject *
make_callbacks (struct gdb_unwind_callbacks *gdb)
{
  struct callbacks_obj *obj = PyObject_New (struct callbacks_obj,
					    &jit_callback_type);
  if (obj != NULL)
    obj->gdb = gdb;
  return (PyObject *) obj;
}

static enum gdb_status
read_debug_info (struct gdb_reader_funcs *self, struct gdb_symbol_callbacks *gdb,
		 void *bytes, long len)
{
  /* We don't support this for now.  */
  return GDB_SUCCESS;
}

static void
free_value (struct gdb_reg_value *v)
{
  free (v);
}

static struct gdb_reg_value *
make_value (void *bytes, Py_ssize_t len)
{
  /* Who knows what size we should use.  */
  struct gdb_reg_value *result = malloc (sizeof (struct gdb_reg_value) - 1
					 + len);
  if (result == NULL)
    {
      PyErr_SetString (PyExc_MemoryError, "couldn't allocate memory for value");
      return NULL;
    }

  result->size = (int) len;
  result->defined = 1;
  result->free = free_value;
  memcpy (&result->value[0], bytes, len);

  return result;
}

static enum gdb_status
unwind (struct gdb_reader_funcs *self, struct gdb_unwind_callbacks *gdb)
{
  PyObject *callbacks;
  enum gdb_status result = GDB_FAIL;

  if (reader == NULL)
    return GDB_FAIL;

  callbacks = make_callbacks (gdb);
  if (callbacks == NULL)
    goto done;

  PyObject *regs = PyObject_CallMethod (reader, "unwind", "(O)",
					callbacks, (char *) 0);

  if (regs == NULL)
    handle_error ();
  else if (PyObject_Not (regs))
    {
      /* Nothing.  */
    }
  else
    {
      /* This should return an array of registers.  A None entry is
	 taken to mean the register isn't available.  */
      PyObject *iter = PyObject_GetIter (regs);
      if (iter == NULL)
	goto done;

      int regno = 0;
      PyObject *one_reg;
      for (; (one_reg = PyIter_Next (iter)) != NULL; ++regno)
	{
	  if (one_reg == Py_None)
	    {
	      Py_DECREF (one_reg);
	      continue;
	    }

	  Py_buffer view;
	  if (PyObject_GetBuffer (one_reg, &view, PyBUF_SIMPLE) >= 0)
	    {
	      struct gdb_reg_value *v = make_value (view.buf, view.len);
	      if (v != NULL)
		gdb->reg_set (gdb, regno, v);
	      PyBuffer_Release (&view);
	    }

	  Py_DECREF (one_reg);
	  if (PyErr_Occurred ())
	    break;
	}

      if (!PyErr_Occurred ())
	result = GDB_SUCCESS;

      Py_DECREF (iter);
    }

 done:
  if (PyErr_Occurred ())
    handle_error ();

  Py_XDECREF (regs);
  Py_DECREF (callbacks);

  return result;
}

static struct gdb_frame_id
get_frame_id (struct gdb_reader_funcs *self, struct gdb_unwind_callbacks *gdb)
{
  PyObject *callbacks;
  /* This method isn't allowed to fail.  So we have to return
     something, even if it is completely bogus.  */
  struct gdb_frame_id result = { 0, 0 };

  if (reader == NULL)
    return result;

  callbacks = make_callbacks (gdb);
  if (callbacks != NULL)
    {
      PyObject *frame_id = PyObject_CallMethod (reader, "get_frame_id", "(O)",
						callbacks, (char *) 0);
      unsigned PY_LONG_LONG code_addr, stack_addr;

      if (frame_id != NULL
	  && PyArg_ParseTuple (frame_id, "KK", &code_addr, &stack_addr) >= 0)
	{
	  result.code_address = code_addr;
	  result.stack_address = stack_addr;
	}

      Py_XDECREF (frame_id);
      Py_DECREF (callbacks);
    }

  if (PyErr_Occurred ())
    handle_error ();

  return result;
}

static void
destroy (struct gdb_reader_funcs *self)
{
  Py_XDECREF (reader);
  Py_DECREF (module);
}

static struct gdb_reader_funcs jit_funcs =
{
  GDB_READER_INTERFACE_VERSION,
  NULL,
  read_debug_info,
  unwind,
  get_frame_id,
  destroy
};

static PyObject *
add_jit (PyObject *self, PyObject *arg)
{
  /* Due to a limitation of the gdb API, there can only be a single
     reader.  The limitation is that the unwind and frame_id methods
     must be paired, but they carry no identity.  However, it's nice
     to let the user replace the reader, so that debugging the reader
     is not crazy.  */
  PyObject *old = reader;

  if (old == NULL)
    {
      old = Py_None;
      Py_INCREF (old);
    }

  reader = arg;
  Py_INCREF (reader);

  return old;
}

static PyMethodDef methods[] =
{
  { "register_jit_reader", add_jit, METH_O, "Register a JIT reader." },
  {NULL, NULL, 0, NULL}
};

struct gdb_reader_funcs *
gdb_init_reader (void)
{
  module = Py_InitModule ("GdbJitReader", methods);
  if (module == NULL)
    {
      handle_error ();
      return NULL;
    }
  
  if (PyType_Ready (&jit_callback_type) < 0)
    {
      Py_DECREF (module);
      handle_error ();
      return NULL;
    }

  return &jit_funcs;
}

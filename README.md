# In Place restarter

A Jupyter kernel proxy which can be useful for inplace restart.

In hpc system for example you might not want to go back through the scheduler. 
it might be useful to restart in place. 

This install a proxy kernel which will forward all the messages to the
underlying kernel, but intercept the `%restart` magic to kill and restart the
underlying kernel.

# Installing:


```
$ pip install inplace_restarter
```

This _must_ be installed on all the environment you want to restart ipykernel in place. 

```
$ python -m inplace_restarter list
In place restarting installed on:
  ✓ 'atk'

Use:python -m inplace_restarter remove [name,[name...]] to remove

In place restarting installable on:
  - 'python3'
  - 'mpl'
  - 'sns'

Use:python -m inplace_restarter install [name,[name...]] to install

Unknown kernel types, does not know how to install:
  ✘ 'bash'
  ✘ 'ir'
  ✘ 'julia-0.6'
```

If prompt toolkit is installed, `python -m inplace_restarter wiz` will open a wizard to install/remove from kernels.

# Gotchas:

Automatic install suppose that the kernelspec path on all the system this will be used is the same. 
This is made to not "Pollute" the kernelspec list; otherwise you will get 2x the number of kernelspec. 
one to launch the proxy and one to launch the inner kernel.












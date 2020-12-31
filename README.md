# In Place restarter

A Jupyter kernel proxy which can be useful for inplace restart.

In hpc system for example you might not want to go back through the scheduler. 
it might be useful to restart in place. 

This install a proxy kernel which will forward all the messages to the
underlying kernel, but intercept the `%restart` magic to kill and restart the
underlying kernel.

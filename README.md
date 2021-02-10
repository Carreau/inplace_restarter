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

It is _recommended_ to install this in all the environment you want this to work
on; if not the original kernelspecs need to refer to the full path of the python
environment you wish to use.

# Usage

You can use the subcommands 

 - `list` to list all the kernels and whether inplace_restarter is installed for
   them.
 - `install`/`remove` followed by kernelspec names to install/remove inplace
   restarted from those.
 - `wiz[ard]`, when prompt_toolkit is installed; this will open a dialog box, to
   let you select the kernels on which to install/remove inplace restarter.

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

# Gotchas:

Automatic install supposes that the kernelspec path on all the system this will be used is the same. 
This is made to not "Pollute" the kernelspec list; otherwise you will get 2x the number of kernelspec. 
one to launch the proxy and one to launch the inner kernel.

# Usage with remote_ikernel

This should be installable on existing `remote_ikernel` spec without further
modifications; Note that on `%restart` this will close all the ssh connections
and re-establish them.

Note that it might be possible to install `remote_ikernel` on an existing
`inplace_restarter` installation in which case the ssh connection will not be
reestablished, and only the remote process will be restarted. Note that this
might be more difficult to deploy due to internal remote_ikernel specifics and
that careful consideration as to whether path involved are with respect to the
local or remote machine working directories, which remote_ikernel might not be
able to properly guess. 

# how to it modify the kernelspecs ?

inplace restart save the current argument of the kernelspec in a new fields and
replace them with the command to start itself.

When started by jupyter; it will attempt to guess what `kernel.json` was used,
extract original command to start the kernel, and introduce itself as a Proxy
between the original client and kernel. When it receives the command for an
inplace restart, it will kill the underlying kernel and start a new one, leaving
original connections to the clients. 

# DEBUG

add `"--RestarterApp.log_level=DEBUG"` in the kernelspec to have debug messages:


```
{
  "argv": [
    ".../bin/python",
    "-m",
    "inplace_restarter",
    ...
    "--RestarterApp.log_level=DEBUG"
  ],
  "display_name": "Python 3",
  "language": "python",
  "restarter_original_argv": [
    ".../bin/python",
    "-m",
    "ipykernel_launcher",
    "-f",
    "{connection_file}"
  ]
}
````

















"""A Kernel Proxy to restart your kernels in place.

use the %restart magic to restart the kernel.

Adapted from MinRK's all the kernels.

How does it work ?

- this setup a proxy that will pass the messages back and forth between the
  client and the kernel.
- when the command `%restart` is intercepted; the proxy will restart the kernel
  instead of executing.

Installation/Removal
====================

Installation, removal is made by modifying the existing kernelspecs.
 - the original lauch argument are stored into a new field named
   ``restarter_original_argv``, and arguments to start self are put in place.
 - when the restarter is called; it will inspect the ``resource_dir`` which is
   given to it and infer the kernelspec that was used; open it, and use the
   ``restarter_original_argv`` parameters to start the original kernel.


Remote ikernel utilisation
==========================

When using remote_ikernel only ipykernel is currently supported for automatic
installation, both the install, remove and wizard command should behave properly
and patch the kernelspec properly. 

Note that inplace_restarter must be installed in the target environment on the
remote machine. 

"""


import json

import os
import sys
from pathlib import Path


import zmq
from ipykernel.kernelapp import IPKernelApp
from ipykernel.kernelbase import Kernel
from IPython.core.usage import default_banner
from jupyter_client import KernelManager
from jupyter_client.kernelspec import find_kernel_specs
from tornado.ioloop import IOLoop
from traitlets import Unicode, Bool
from zmq.eventloop.future import Context

__version__ = "0.0.7"

NAME = "inplace_restarter"

RESTARTER_KEY = "restarter_original_argv"
REMOTE_IKERNEL_KEY = "remote_ikernel_argv"

import os.path


class SwapArgKernelManager(KernelManager):
    """
    Kernel manager that rewrite the start command to avoid recursion.

    Indeed the original kernelspec will start us, and we will read it to start
    ipykernel, so we need to swap
    -m <us>, for -m ipykernel
    """

    @property
    def extra_env(self):
        return None

    def format_kernel_cmd(self, *args, **kwargs):

        class O:
            argv = []
            env = []
            resource_dir = None
            pass

        if self._kernel_spec is None:
            self._kernel_spec = O()
            self.kernel_spec.argv = [
                sys.executable,
                "-m",
                "ipykernel_launcher",
                "-f",
                "{connection_file}",
            ]
            # print(self.kernel_spec.argv)

        else:

            data = (Path(self.kernel_spec.resource_dir) / "kernel.json").read_text()

            data = json.loads(data)
            origin = data.get(RESTARTER_KEY)
            assert isinstance(origin, list)
            # self.kernel_cmd = origin
            data = (Path(self.kernel_spec.resource_dir) / "kernel.json").read_text()
            self.kernel_spec.argv = json.loads(data)[RESTARTER_KEY]
        res = super().format_kernel_cmd(*args, **kwargs)
        assert NAME not in res
        return res


class KernelProxy(object):
    """A proxy for a single kernel


    Hooks up relay of messages on the shell channel.
    """

    def __init__(self, manager, shell_upstream):
        self.manager = manager
        self.shell = self.manager.connect_shell()
        self.shell_upstream = shell_upstream
        self.iopub_url = self.manager._make_url("iopub")
        IOLoop.current().add_callback(self.relay_shell)

    async def relay_shell(self):
        """Coroutine for relaying any shell replies"""
        while True:
            msg = await self.shell.recv_multipart()
            self.shell_upstream.send_multipart(msg)


class Proxy(Kernel):
    """Kernel class for proxying ALL THE KERNELS YOU HAVE"""

    implementation = "IPython Kernel Restarter"
    implementation_version = __version__
    language_info = {
        "name": "Python",
        "mimetype": "text/python",
    }

    banner = default_banner

    _ipr_parent = None
    target = Unicode("path the the kernelspec (can be self or another one)")
    rd = Unicode(None, config=True, allow_none=True)
    resource_dir_workaround = Unicode(None, config=True, allow_none=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.log.setLevel(self.parent.log_level)

        self.future_context = ctx = Context()
        self.iosub = ctx.socket(zmq.SUB)
        self.iosub.subscribe = b""
        self.shell_stream = self.shell_streams[0]
        self.kernel = None
        self.log.debug(f"Started with resource_dir={sys.argv}")
        self.log.debug(f"Started with resource_dir={self.rd}")
        self.log.debug(
            f"Started with resource_dir_workaround={self.resource_dir_workaround}"
        )

        spec_dir = self.rd
        if spec_dir is None:
            import warnings

            spec_dir = self.resource_dir_workaround

            warnings.warn(
                "--Proxy.rd is None when starting with inplace_restarter, this "
                "may not work properly. Falling back to the value of "
                "resource_dir_workaround " + str(spec_dir),
                UserWarning,
            )
        if spec_dir is None:
            self.target = "auto_ipykernel"
        else:
            self.target = os.path.split(spec_dir)[-1]

    def start(self):
        self.log.debug("Starting restarter loop")
        super().start()
        loop = IOLoop.current()
        loop.add_callback(self.relay_iopub_messages)
        self.start_kernel()

    async def relay_iopub_messages(self):
        """Coroutine for relaying IOPub messages from all of our kernels"""
        while True:
            msg = await self.iosub.recv_multipart()
            self.iopub_socket.send_multipart(msg)

    def start_kernel(self):
        """Start a new kernel"""
        self.log.debug("Parent connection file: %s", self.parent.connection_file)
        base, ext = os.path.splitext(self.parent.connection_file)
        cf = "{base}-restartable{ext}".format(
            base=base,
            ext=ext,
        )
        self.log.debug("Child connection file: %s", cf)
        manager = SwapArgKernelManager(
            kernel_name=self.target,
            session=self.session,
            context=self.future_context,
            connection_file=cf,
            parent=self,
        )
        manager.start_kernel()
        self.kernel = KernelProxy(
            manager=manager,
            shell_upstream=self.shell_stream,
        )
        self.iosub.connect(self.kernel.iopub_url)
        return [self.kernel]

    def get_kernel(self):
        """Get a kernel, start it if it doesn't exist"""
        if self.kernel is None:
            self.start_kernel()
        return self.kernel

    def set_parent(self, ident, parent):
        # record the parent message
        self._ipr_parent = parent
        return super().set_parent(ident, parent)

    def _publish_status(self, status):
        """Disabling publishing status messages for relayed

        Status messages will be relayed from the actual kernels.
        """
        if self._ipr_parent and self._ipr_parent["header"]["msg_type"] in {
            "execute_request",
            "inspect_request",
            "complete_request",
        }:
            self.log.debug("suppressing %s status message.", status)
            return
        else:
            return super()._publish_status(status)

    def intercept_kernel(self, stream, ident, parent):

        content = parent["content"]
        cell = content["code"]
        if cell == "%restart":
            ## ask kernel to do nothing but still send an empty reply to flush ZMQ
            parent["content"]["code"] = ""
            parent["content"]["silent"] = True

        res = self.relay_to_kernel(stream, ident, parent)

        if cell == "%restart":

            self.kernel.manager.shutdown_kernel(now=False, restart=True)
            self.kernel = None

        return res

    def relay_to_kernel(self, stream, ident, parent):
        """Relay a message to a kernel

        Gets the `>kernel` line off of the cell,
        finds the kernel (starts it if necessary),
        then relays the request.
        """

        kernel = self.get_kernel()
        self.log.debug(
            "Relaying %s to %s",
            parent["header"]["msg_type"],
            self.target,
        )
        self.session.send(kernel.shell, parent, ident=ident)

    execute_request = intercept_kernel
    inspect_request = relay_to_kernel
    complete_request = relay_to_kernel

    def do_shutdown(self, restart):
        if self.kernel:  # give up if just restarted and not yes up.
            self.kernel.manager.shutdown_kernel(now=False, restart=restart)
        return super().do_shutdown(restart)


class RestarterApp(IPKernelApp):

    kernel_class = Proxy
    # disable IO capture
    outstream_class = None

    def _log_level_default(self):
        return 0


DEFAULT_COMMAND = [
    sys.executable,
    "-m",
    "inplace_restarter",
    "-f",
    "{connection_file}",
    "--Proxy.rd={resource_dir}",
]


def remote_ikernel_installed(spec):
    """
    check if the spec uses remote ikernel and we are installed in the spec.
    """
    assert spec.get(REMOTE_IKERNEL_KEY, None) is not None, spec
    argv = spec.get("argv")
    kernel_cmd = None
    for i, item in enumerate(argv):
        if item == "--kernel_cmd":
            kernel_cmd = argv[i + 1]
            if "-m inplace_restart" in kernel_cmd:
                return "Remote-Inplace"
            elif "-m ipykernel_launcher" in kernel_cmd:
                return "installable"
            else:
                return "unknown"

    raise ValueError("Don't understand spec", spec)


def installed(spec):
    is_remote_ikernel = spec.get(REMOTE_IKERNEL_KEY, None) is not None
    orig = spec.get(RESTARTER_KEY, None)
    if is_remote_ikernel:
        res = remote_ikernel_installed(spec)
        return res
    if orig is None:
        return "installable"
    else:
        if spec.get("argv")[1:6] == DEFAULT_COMMAND[1:6]:
            return "installed"
        else:
            return "unknown"


def _list(specs):
    m = {"Installed": [], "Installable": [], "Unknown": [], "Remote-Inplace": []}
    for name, path in specs.items():
        path = Path(path) / "kernel.json"
        data = json.loads(path.read_text())

        status = installed(data)
        if status == "installable":
            m["Installable"].append(name)
        elif status == "Remote-Inplace":
            m["Remote-Inplace"].append(name)
        elif status == "installed":
            m["Installed"].append(name)
        else:
            m["Unknown"].append(name)
    return m


def list_target(specs):
    m = _list(specs)

    if m["Installed"]:
        print("In place restarting installed on:")
        for kernel in m["Installed"]:
            print(f"  ✓ {kernel!r}")
        print("")
        print("Use:python -m inplace_restarter remove [name,[name...]] to remove")
        print("")

    if m["Remote-Inplace"]:
        print("Remote kernel with inplace restarter")
        for kernel in m["Remote-Inplace"]:
            print(f"  ✓ {kernel!r}")
        print("")

    if m["Installable"]:
        print("In place restarting installable on:")
        for kernel in m["Installable"]:
            print(f"  - {kernel!r}")
        print("")
        print("Use:python -m inplace_restarter install [name,[name...]] to install")
        print("")
    if m["Unknown"]:
        print("Unknown kernel types, does not know how to install:")
        for kernel in m["Unknown"]:
            print(f"  ✘ {kernel!r}")
        print("")

    # print(" :", name)
    # print("✓:", name)
    # print("✘:", name)


def _swap_on_remote(spec, source, target):
    """
    modify and return he spec, swapping `source` for `target` on the remote
    command
    """
    assert spec.get(REMOTE_IKERNEL_KEY, None) is not None, spec
    argv = spec.get("argv")
    kernel_cmd = None
    kernel_cmd_index = None

    kernel_cmd_index = argv.index("--kernel_cmd") + 1
    kernel_cmd = argv[kernel_cmd_index]
    assert f"-m {source}" in kernel_cmd

    new_kernel_cmd = kernel_cmd.split(" ")
    ind = new_kernel_cmd.index(source)
    new_kernel_cmd[ind] = target
    argv[kernel_cmd_index] = " ".join(new_kernel_cmd)
    return spec


def install_on(name, specs):
    path = Path(specs[name]) / "kernel.json"
    data = json.loads(path.read_text())
    if installed(data) != "installable":
        print("not installable on ", name)
    else:
        is_remote_ikernel = data.get(REMOTE_IKERNEL_KEY, None) is not None
        if is_remote_ikernel:
            data = _swap_on_remote(data, "ipykernel_launcher", "inplace_restarter")
        else:
            data[RESTARTER_KEY] = data["argv"]
            data["argv"] = DEFAULT_COMMAND + [
                f"--Proxy.resource_dir_workaround={specs[name]}"
            ]
        path.write_text(json.dumps(data, indent=2))


def remove_from(name, specs):
    path = Path(specs[name]) / "kernel.json"
    data = json.loads(path.read_text())
    status = installed(data)
    if status not in ("installed", "Remote-Inplace"):
        print("not installed on ", name)
    else:
        is_remote_ikernel = data.get(REMOTE_IKERNEL_KEY, None) is not None
        if is_remote_ikernel:
            data = _swap_on_remote(data, "inplace_restarter", "ipykernel_launcher")
        else:
            data["argv"] = data[RESTARTER_KEY]
            del data[RESTARTER_KEY]
        text = json.dumps(data, indent=2)
        path.write_text(text)


def wiz(specs):
    from prompt_toolkit.application.current import get_app
    from prompt_toolkit.shortcuts.dialogs import _create_app
    from prompt_toolkit.layout.containers import HSplit
    from prompt_toolkit.widgets import Button, CheckboxList, Dialog, Label

    def checkboxlist_dialog(
        title="",
        text="",
        ok_text="Ok",
        cancel_text="Cancel",
        values=None,
        selected=None,
    ):
        """
        Display a simple list of element the user can choose multiple values amongst.

        Several elements can be selected at a time using Arrow keys and Enter.
        The focus can be moved between the list and the Ok/Cancel button with tab.
        """
        if values is None:
            values = []

        def ok_handler() -> None:
            get_app().exit(result=cb_list.current_values)

        cb_list = CheckboxList(values)
        for s in selected:
            cb_list.current_values.append(s)
        dialog = Dialog(
            title=title,
            body=HSplit(
                [Label(text=text, dont_extend_height=True), cb_list],
                padding=1,
            ),
            buttons=[
                Button(text=ok_text, handler=ok_handler),
                Button(text=cancel_text, handler=lambda: get_app().exit()),
            ],
            with_background=True,
        )
        style = None
        return _create_app(dialog, style), cb_list

    m = _list(specs)

    results_array, cb = checkboxlist_dialog(
        title="In place restarter:",
        text="Select/deselect kernels on which to install, Tab navigate to OK/Cancel",
        values=[
            (x, x) for x in m["Installed"] + m["Remote-Inplace"] + m["Installable"]
        ],
        selected=m["Installed"] + m["Remote-Inplace"],
    )

    final = results_array.run()
    if final is None:
        return
    else:
        for k in m["Installed"] + m["Remote-Inplace"]:
            if k not in final:
                print("Removing from", k)
                remove_from(k, specs)
        for k in m["Installable"]:
            if k in final:
                print("Installing on", k)
                install_on(k, specs)


def main():

    if len(sys.argv) > 1 and sys.argv[1] == "install":
        specs = dict(find_kernel_specs().items())
        for name in sys.argv[2:]:
            install_on(name, specs)
    elif len(sys.argv) > 1 and sys.argv[1] == "remove":
        specs = dict(find_kernel_specs().items())
        for name in sys.argv[2:]:
            remove_from(name, specs)
    elif len(sys.argv) > 1 and sys.argv[1] == "list":
        specs = dict(find_kernel_specs().items())
        list_target(specs)
    elif len(sys.argv) > 1 and sys.argv[1] in ("wiz", "wizard"):
        specs = dict(find_kernel_specs().items())
        wiz(specs)
    else:
        RestarterApp.launch_instance()


if __name__ == "__main__":
    main()

"""A Kernel Proxy to restart your kernels in place.

use the %restart magic.


Adapted from MinRK's all the kernels.
"""

import os
import sys

from tornado.ioloop import IOLoop

import zmq
from zmq.eventloop import ioloop
from zmq.eventloop.future import Context

from traitlets import Dict, Unicode

from jupyter_client import KernelManager
from jupyter_client.kernelspec import find_kernel_specs
from ipykernel.kernelbase import Kernel
from ipykernel.kernelapp import IPKernelApp, IPythonKernel

from IPython.core.usage import default_banner

__version__ = "0.0.1.dev"

NAME = "inplace_restarter"


class SwapArgKernelManager(KernelManager):
    """
    Kernel manager that rewrite the start command to avoid recursion.

    Indeed the original kernelspec will start us, and we will read it to start ipykernel, so we need to swap
    -m <us>, for -m ipykernel
    """

    def format_kernel_cmd(self, *args, **kwargs):
        res = super().format_kernel_cmd(*args, **kwargs)
        res = ["ipykernel" if x == NAME else x for x in res]
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
    target = Unicode("wuup", config=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.future_context = ctx = Context()
        self.iosub = ctx.socket(zmq.SUB)
        self.iosub.subscribe = b""
        self.shell_stream = self.shell_streams[0]
        self.kernel = None
        if self.target is None:
            raise ValueError(
                "--Proxy.target is required when starting with inplace_restarter"
            )

    def start(self):
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
        base, ext = os.path.splitext(self.parent.connection_file)
        cf = "{base}-restartable{ext}".format(
            base=base,
            ext=ext,
        )
        manager = SwapArgKernelManager(
            kernel_name=self.target.split("/")[-2],
            session=self.session,
            context=self.future_context,
            connection_file=cf,
        )
        manager.start_kernel()
        self.kernel = KernelProxy(manager=manager, shell_upstream=self.shell_stream)
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
            self.target.split("/")[-2],
        )
        self.session.send(kernel.shell, parent, ident=ident)

    execute_request = intercept_kernel
    inspect_request = relay_to_kernel
    complete_request = relay_to_kernel

    def do_shutdown(self, restart):
        self.kernel.manager.shutdown_kernel(now=False, restart=restart)
        return super().do_shutdown(restart)


class RestarterApp(IPKernelApp):

    kernel_class = Proxy
    # disable IO capture
    outstream_class = None

    def _log_level_default(self):
        return 0


from pathlib import Path

DEFAULT_COMMAND = ["-m", "ipykernel_launcher", "-f", "{connection_file}"]
RESTARTER_COMMAND = ["-m", "inplace_restarter", "-f", "{connection_file}"]

import json


def list_target(specs):
    m = {"Installed": [], "Installable": [], "Unknown": []}
    for name, path in specs.items():
        name = repr(name)
        path = Path(path) / "kernel.json"
        data = json.loads(path.read_text())

        argv = data["argv"]

        if argv[1:5] == DEFAULT_COMMAND:
            m["Installable"].append(name)
        elif argv[1:5] == RESTARTER_COMMAND:
            m["Installed"].append(name)
        else:
            m["Unknown"].append(name)
    if m["Installed"]:
        print("In place restarting installed on:")
        for kernel in m["Installed"]:
            print(f"  ✓ {kernel}")
        print("")
        print("Use:python -m inplace_restarter remove [name,[name...]] to remove")
        print("")
    if m["Installable"]:
        print("In place restarting installable on:")
        for kernel in m["Installable"]:
            print(f"  - {kernel}")
        print("")
        print("Use:python -m inplace_restarter install [name,[name...]] to install")
        print("")
    if m["Unknown"]:
        print("Unknown kernel types, does not know how to install:")
        for kernel in m["Unknown"]:
            print(f"  ✘ {kernel}")
        print("")

    # print(" :", name)
    # print("✓:", name)
    # print("✘:", name)


def install_on(name, specs):
    path = Path(specs[name]) / "kernel.json"
    data = json.loads(path.read_text())
    argv = data["argv"]
    if not argv[1:5] == DEFAULT_COMMAND:
        print("not installable on ", name)
    else:
        data["argv"][2] = "inplace_restarter"
        data["argv"].append(f"--Proxy.target={path}")
        path.write_text(json.dumps(data, indent=2))


def remove_from(name, specs):
    path = Path(specs[name]) / "kernel.json"
    data = json.loads(path.read_text())
    argv = data["argv"]
    if not argv[1:5] == RESTARTER_COMMAND:
        print("not installed on ", name)
    else:
        data["argv"][2] = "ipykernel_launcher"
        new_argv = [a for a in data["argv"] if not a.startswith("--Proxy.target=")]
        data["argv"] = new_argv
        text = json.dumps(data, indent=2)
        path.write_text(text)


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

    else:
        RestarterApp.launch_instance()


if __name__ == "__main__":
    main()

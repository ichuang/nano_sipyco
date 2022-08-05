#!/usr/bin/env python3
'''
Very rudimentary NDSP framework which does *not* use sipyco, but can run
a remote procedure, accepting basic python data types and returning basic
python data types.

Just uses the python socketserver library.  Does not require numpy.

This is useful for running on rudimentary servers, e.g. old Raspberry Pi's
or embedded processors.
'''

import sys
import logging
import inspect
import argparse
import traceback
import socketserver

logger = logging.getLogger(__name__)
VERBOSE_DEBUG = True

#-----------------------------------------------------------------------------

class MyPyon:
    '''
    Dummy replacement for sipyco.pyon (python object notation) which returns a string version
    of a python object
    '''
    def __init__(self):
        return

    def encode(self, obj):
        return repr(obj)

    def decode(self, line):
        try:
            obj = line.decode()	# default - return string
        except Exception as err:
            obj = line
        if line.startswith("{") or line.startswith("["):
            try:
                obj = eval(line)
            except Exception as err:
                pass
        return obj

#-----------------------------------------------------------------------------

class NanoNDSPHandler(socketserver.StreamRequestHandler):
    """
    Handler for NDSP server which does not need sipyco, and only uses python sockets.
    This version does not use asyncio; it is a handler for a TCP/IP socketserver.

    Basic protocol:
    
    [MyServer] Received 1: 'b'ARTIQ pc_rpc\n''
    [MyServer] Received 2: 'b'example_adder\n''
    [MyServer] Received 3: 'b'{"action": "call", "name": "add", "args": (4, 9), "kwargs": {}}\n''
    [MyServer] sending: '{"status": "ok", "ret": 13}'
    [MyServer] Received 4: 'b'''

    """
    _init_string = b"ARTIQ pc_rpc\n"

    @staticmethod
    def _document_function(function):
        """
        Turn a function into a tuple of its arguments and documentation.

        Allows remote inspection of what methods are available on a local device.

        Args:
            function (Callable): a Python function to be documented.

        Returns:
            Tuple[dict, str]: tuple of (argument specifications,
            function documentation).
            Any type annotations are converted to strings (for PYON serialization).
        """
        argspec_dict = dict(inspect.getfullargspec(function)._asdict())
        # Fix issue #1186: PYON can't serialize type annotations.
        if any(argspec_dict.get("annotations", {})):
            argspec_dict["annotations"] = str(argspec_dict["annotations"])
        return argspec_dict, inspect.getdoc(function)

    def _process_action(self, target, obj):
        '''
        Perform requested action (specified in obj) to specified target
        '''
        try:
            if obj["action"] == "get_rpc_method_list":
                members = inspect.getmembers(target, inspect.ismethod)
                doc = {
                    "docstring": inspect.getdoc(target),
                    "methods": {}
                }
                for name, method in members:
                    if name.startswith("_"):
                        continue
                    method = getattr(target, name)
                    doc["methods"][name] = self._document_function(method)
                logger.debug("RPC docs for %s: %s", target, doc)
                return doc
            elif obj["action"] == "call":
                logger.debug(f"calling {obj}")
                if 0:
                    return None
                else:
                    method = getattr(target, obj["name"])
                    ret = method(*obj["args"], **obj["kwargs"])
                    return ret
            else:
                raise ValueError("Unknown action: {}"
                                 .format(obj["action"]))

        except Exception as err:
            raise

    def _process_and_pyonize(self, target, obj):
        '''
        Call target procedure, encode return using pyon, and return dict with status ok
        '''
        try:
            return self.server.pyon.encode({
                "status": "ok",
                "ret": self._process_action(target, obj)
            })
        except Exception as err:
            print(f"[NanoNDSPServer] Error!  {err} at {traceback.format_exc()}")
            return self.server.pyon.encode({
                "status": "failed",
                "exception": str(err),
            })

    def handle(self):
        reader = self.rfile        # self.rfile is a file-like object created by the handler
        writer = self.wfile
        pyon = self.server.pyon

        try:
            linecnt = 0
            line = reader.readline()
            if line != self._init_string:
                return

            linecnt += 1
            if VERBOSE_DEBUG:
                print(f"[MyServer] Received {linecnt}: '{line}'")

            obj = {
                "targets": sorted(self.server.targets.keys()),
                "description": self.server.description
            }
            line = pyon.encode(obj) + "\n"
            writer.write(line.encode())
            line = reader.readline()
            if not line:
                return

            linecnt += 1
            if VERBOSE_DEBUG:
                print(f"[MyServer] Received {linecnt}: '{line}'")

            target_name = line.decode()[:-1]
            try:
                target = self.server.targets[target_name]
            except KeyError:
                return

            if callable(target):
                target = target()

            valid_methods = inspect.getmembers(target, inspect.ismethod)
            valid_methods = {m[0] for m in valid_methods}
            #if self.builtin_terminate:
            #    valid_methods.add("terminate")
            msg = (pyon.encode(valid_methods) + "\n").encode()
            if VERBOSE_DEBUG:
                print(f"[MyServer] sending msg={msg}")
            writer.write(msg)

            while True:
                line = reader.readline()

                linecnt += 1
                if VERBOSE_DEBUG:
                    print(f"[MyServer] Received {linecnt}: '{line}'")

                if not line:
                    break
                reply = self._process_and_pyonize(target, pyon.decode(line.decode()))

                if VERBOSE_DEBUG:
                    print(f"[MyServer] sending: '{reply}'")
                writer.write((reply + "\n").encode())
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            # May happen on Windows when client disconnects
            pass
        finally:
            writer.close()

#-----------------------------------------------------------------------------

class NanoNDSPServer(socketserver.TCPServer):
    '''
    TCP/IP socket server for NDSP.
    This version is single-thread, but could re-mix this to make it threaded.
    '''
    allow_reuse_address = True
    def __init__(self, targets, description="", host="localhost", port=3478):
        '''
        targets = (dict) dict of {procedure_name, <procedure>, ...}
        description = (str) string description of this server
        host = (str) hostname or IP address to bind port on
        port = (int) TCP/IP port number to use
        '''
        self.pyon = MyPyon()
        self.targets = targets
        self.description = description
        super().__init__((host, port), NanoNDSPHandler)

#-----------------------------------------------------------------------------

def example_main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--port", help="server port", default=3478, type=int)
    parser.add_argument("--host", help="server hostname", default="localhost", type=str)

    args = parser.parse_args()

    class ExampleAdder:
        def __init__(self):
            print("ExampleAdder initialized")
    
        def add(self, a, b):
            '''
            Add two numbers and return result
            '''
            return a+b
    
        def print(self, msg):
            '''
            Print message
            '''
            print(msg)
    
    dev = ExampleAdder()
    print(f"Starting sample NDSP server on port {args.port}")
    sys.stdout.flush()

    targets = {"example_adder": dev}
    description = "example adder nano_aqctl"

    with NanoNDSPServer(targets, description, args.host, args.port) as server:
        server.serve_forever()

if __name__ == "__main__":
    example_main()

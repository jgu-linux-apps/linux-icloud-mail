import os
import sys


def main():
    from .app import App
    app = App()
    drv = os.environ.get("LIM_TEST_DRIVER")
    if drv:  # developer test hook
        import importlib.util
        spec = importlib.util.spec_from_file_location("lim_driver", drv)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        app.connect("startup", lambda a: __import__("gi.repository.GLib", fromlist=["GLib"]).timeout_add(
            500, lambda: (mod.run(a), False)[1]))
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())

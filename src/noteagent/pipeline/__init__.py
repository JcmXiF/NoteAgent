"""The pipeline: one persistent run from intent to written pages.

The run is the core's object. The interface starts it, watches it and approves
its plan; a future conversation can reach the same object by id. Import the leaf
modules (``run``, ``store``, ``lock``, ``service``) directly.
"""

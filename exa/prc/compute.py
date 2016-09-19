# -*- coding: utf-8 -*-
# Copyright (c) 2015-2016, Exa Analytics Development Team
# Distributed under the terms of the Apache License 2.0
"""
Computational Resources
########################
"""


class GPU(object):
    """Graphics Processing Unit."""
    def __init__(self, name):
        self.name = name

class MIC(object):
    """Many Integrated Core processors (e.g. Intel MIC, )."""
    pass


class Resource(object):
    """A computing resource ("node")."""
    def __init__(self, name, tasks, gpus, mics, mem):
        self.name = name
        self.tasks = tasks
        self.gpus = gpus
        self.mics = mics
        self.mem = mem


class ComputeResources(object):
    pass

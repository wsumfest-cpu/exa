# -*- coding: utf-8 -*-
# Copyright (c) 2015-2017, Exa Analytics Development Team
# Distributed under the terms of the Apache License 2.0
"""
Containers
########################
This module provides the generic :class:`~exa.container.Container` object.
Containers are used to store a collection of data objects (such as scalars,
lists, arrays, dataframes, etc.). By default the :class:`~exa.container.Container`
enables investigation of the metadata of the data objects it holds. The object
also provides methods for saving the data to disk in common formats such as HDF.
For well structured data such as dataframes, the :class:`~exa.container.Container`
can identify relationships (i.e. relationships between index names and column
names of different dataframes). The :class:`~exa.container.Container` is
extensible and can be used to construct a unified API for a data specific task.
"""
import tables
import warnings
import numpy as np
import pandas as pd
from uuid import uuid4
from sys import getsizeof
from exa.typed import TypedClass, Typed
from .data import _spec_name, _forbidden
if not hasattr(pd.Series, "items"):
    pd.Series.items = pd.Series.iteritems


class Container(TypedClass):
    """
    A storage object for data such as scalars (ints, floats, strs), arrays
    (lists, numpy objects), and structured data (pandas series and dataframe
    objects).

    TODO
    """
    default_prefix = Typed(str, doc="Default prefix for container args.")
    meta = Typed(dict, doc="Document metadata")

    def info(self):
        """
        Display information about the data objects.
        """
        df = []
        for name, item in self._items():
            typ = item.__class__.__name__
            size = float(getsizeof(item))/(1024.0**2)
            if hasattr(item, "shape"):
                shape = str(item.shape)
            elif hasattr(item, "size"):
                if callable(item.size):
                    shape = str(item.size().to_dict())
            else:
                try:
                    shape = str(len(item))
                except (TypeError, AttributeError):
                    shape = "nan"
            df.append([name, typ, size, shape])
        columns = ("attribute", "type", "size (MiB)", "shape")
        return pd.DataFrame(df, columns=columns).set_index("attribute").sort_index()

    def network(self):
        """
        Display information about relationships between data objects.

        Dataframes with unique indices are like database tables where the index
        is a primary key. If, in another dataframe, a column with the same name
        as that index exists, it is akin to a database table's foreign key.
        Foreign keys define relationships between dataframes.
        """
        # Pick a default plotting system (bokeh, mpl, seaborn, etc.)
        raise NotImplementedError()

    def memory_usage(self):
        """
        Return total (estimated) memory usage (in MiB).
        """
        return self.info()['size (MiB)'].sum()

    def to_hdf(self, path, mode='a', append=None, sparse=0.95,
               complevel=0, complib=None, fletcher32=False, **kwargs):
        """
        Save the container's data to an HDF file.

        Exa specific data objects save their data as well as any additional
        (typed) attributes (e.g. dictionary of metadata). This method addtionally
        handles Python built-in types and numpy objects. Built-in types are
        saved as attributes of the special storer (default '__exa_storer__').
        Numpy arrays are saved using `pytables`_ machinery.

        Args:
            path (str): File path
            mode (str): Writing mode
            append (list): List of object names which are appendable
            sparse (float): Minimum density for sparse data structure
            complevel (int): Compression level
            complib (str): Compression library
            fletcher32 (bool): Checksum
            kwargs: Additional keyword arguments to be passed to put/append

        Warning:
            Existing data objects with the same name in the current HDF file
            (if applicable) will be overwritten unless the correct mode and append
            arguments are used. Scalar variables will always be overwritten (if
            they exist) or added to the collection of existing scalars (if they
            don't). Numpy and Python objects do not support append operations.

        See Also:
            Additional arguments can be found in `pandas`_ documentation.

        Note:
            Passing the keyword argument warn = False will prevent warnings from
            being shown.

        .. _pytables: http://www.pytables.org
        .. _pandas: https://pandas.pydata.org
        """
        warn = kwargs.pop("warn", True)
        # On first pass, identify which data objects we can save
        # and what method (pandas/pytables) must be used.
        numpy_save = {}
        pandas_save = {}
        special_save = {}
        for name, data in self._items():
            if isinstance(data, np.ndarray):
                numpy_save[name] = data
            elif isinstance(data, (pd.Series, pd.DataFrame, pd.SparseSeries,
                                   pd.SparseDataFrame)):
                pandas_save[name] = data
            elif isinstance(data, (str, int, float, complex, dict, list, tuple)):
                special_save[name] = data
            elif data is not None and warn == True:
                warnings.warn("Data object '{}' ({}) not saved (unsupported).".format(name, type(data)))
        # First save numpy objects
        if len(numpy_save) > 0:
            filters = tables.Filters(complib=complib, complevel=complevel)
            store = tables.open_file(path, mode=mode, filters=filters)
            for name, data in numpy_save.items():
                store.create_carray("/", name, obj=data)
            store.close()
        # Save remaining objects
        store = pd.HDFStore(path, mode=mode, complevel=complevel,
                            complib=complib, fletcher32=fletcher32)
        if _spec_name not in store:
            store.put(_spec_name, pd.Series())
        special = store.get_storer(_spec_name).attrs
        for name, data in special_save.items():
            special[name] = data
        for name, data in pandas_save.items():
            if ((isinstance(append, (list, tuple)) and name in append) or
                append == True or name == append):
                data.to_hdf(store, name, append=True, close=False, **kwargs)
            else:
                data.to_hdf(store, name, close=False)
        store.close()

    @classmethod
    def from_hdf(cls, path, complib=None, complevel=0, fletcher32=False):
        """
        Load a container from an HDF file.

        Args:
            path (str): Path to HDF file
        """
        kwargs = {}
        # First load pandas-like and special objects
        store = pd.HDFStore(path, mode="r", complib=complib,
                            complevel=complevel, fletcher32=fletcher32)
        if _spec_name in store:
            for name, data in vars(store.get_storer(_spec_name).attrs).items():
                if name in _forbidden:
                    continue
                kwargs[name] = data
        for name, data in store.items():
            kwargs[name] = data
        store.close()
        # Second load numpy-like objects
        filters = tables.Filters(complib=complib, complevel=complevel)
        store = tables.open_file(path, mode="r", filters=filters)
        for data in store.walk_nodes():
            if hasattr(data, "name") and data.name not in kwargs:
                kwargs[data.name] = data.read()
        store.close()
        return cls(**kwargs)

    def _network(self):
        """Helper function to generate the nodes and edges of the network."""
        nodes = {}
        edges = []
        for name, item in self._items():
            if isinstance(item, pd.DataFrame):
                nodes[name] = (item.index.name, item.columns.values)
        for key0, (index_name0, column_names0) in nodes.items():
            for key1, (index_name1, column_names1) in nodes.items():
                if key0 == key1:
                    continue
                if ((index_name0 is not None and any(col == index_name0 for col in column_names1)) or
                    (index_name1 is not None and any(col == index_name1 for col in column_names0))):
                    pair = sorted((key0, key1))
                    if pair not in edges:
                        edges.append(pair)
        return sorted(nodes.keys()), sorted(edges)

    def _items(self, dct=None, include_keys=False):
        """
        Iterator for looping over data objects in the current container.
        """
        if dct is None:
            dct = vars(self)
        for key, data in dct.items():
            # Determine the correct name to use
            if (key.startswith("_") and hasattr(self.__class__, key[1:]) and
                isinstance(getattr(self.__class__, key[1:]), property)):
                name = key[1:]
            else:
                name = key
            if include_keys == True:
                yield key, name, data
            else:
                yield name, data

    def __contains__(self, key):
        if hasattr(self, key):
            return True

    def __eq__(self, other):
        for name, data in self._items():
            if name in other and not np.all(getattr(other, name) == data):
                return False
        return True

    def __len__(self):
        return len(vars(self).keys())

    def __delitem__(self, key):
        if key in vars(self):
            delattr(self, key)

    def __sizeof__(self):
        """Note that this function must return a Python integer."""
        return int(np.ceil(self.memory_usage()*1024**2))

    def __init__(self, *args, **kwargs):
        self.default_prefix = kwargs.pop("default_prefix", "obj_")
        self.meta = kwargs.pop("meta", None)
        for arg in args:
            do = True
            # The while check is used to make sure names do not overlap...
            # ... chances of that are incredibly small so I am not sure it
            # is necessary but it doesn't really affect performance???
            while do:
                name = self.default_prefix + uuid4().hex
                if not hasattr(self, name):
                    setattr(self, name, arg)
                    do = False
        for name, data in kwargs.items():
            setattr(self, name, data)

    def __repr__(self):
        n = self.__class__.__name__
        size = np.round(self.memory_usage(), 3)
        return "{}(data={}, size (MiB)={})".format(n, len(self), size)


def concatenate(*containers, **kwargs):
    """
    Concatenate containers' data and create a new container object.

    Args:
        containers (iterable): Collection of container objects to concatenate
        axis (int, dict): Axis along which to concatenate (dictionary with data object name, int pairs)
        join (str): How to join indices of other axis (default, 'outer')
        ignore_index (bool): Reset concatenation index values (default False)
    """
    raise NotImplementedError()


##    def network(self, figsize=(14, 9), fig=True):
##        conn_types = ['index-index', 'index-column']
##        conn_colors = mpl.color_palette(n=len(conn_types))
##        conn = dict(zip(conn_types, conn_colors))
##
##        def get_node_type_color(obj):
##            typs = [Field, SparseSeries, DataFrame, SparseDataFrame, Series, pd.DataFrame, pd.Series]
##            cols = mpl.color_palette(n=len(typs))
##            for typ, col in zip(typs, cols):
##                if isinstance(obj, typ):
##                    return '.'.join((typ.__module__, typ.__name__)), col
##            return 'other', 'gray'
##
##        def legend(items, name, loc, ax):
##            proxies = []
##            descriptions = []
##            for label, color in items:
##                if label == 'column-index':
##                    continue
##                if name == 'Data Type':
##                    line = mpl.sns.mpl.lines.Line2D([], [], linestyle='none', color=color, marker='o')
##                else:
##                    line = mpl.sns.mpl.lines.Line2D([], [], linestyle='-', color=color)
##                proxies.append(line)
##                descriptions.append(label)
##            lgnd = ax.legend(proxies, descriptions, title=name, loc=loc, frameon=True)
##            lgnd_frame = lgnd.get_frame()
##            lgnd_frame.set_facecolor('white')
##            lgnd_frame.set_edgecolor('black')
##            return lgnd, ax
##
##        info = self.info()
##        info = info[info['type'] != '-']
##        info['size'] *= 15000/info['size'].max()
##        info['size'] += 5000
##        node_size_dict = info['size'].to_dict()      # Can pull all nodes from keys
##        node_class_name_dict = info['type'].to_dict()
##        node_type_dict = {}    # Values are tuple of "underlying" type and color
##        node_conn_dict = {}    # Values are tuple of connection type and color
##        items = self._data().items()
##        for k0, v0 in items:
##            n0 = k0[1:] if k0.startswith('_') else k0
##            node_type_dict[n0] = get_node_type_color(v0)
##            for k1, v1 in items:
##                if v0 is v1:
##                    continue
##                n1 = k1[1:] if k1.startswith('_') else k1
##                for name in v0.index.names:    # Check the index of data object 0 against the index
##                    if name is None:           # and columns of data object 1
##                        continue
##                    if name in v1.index.names:
##                        contyp = 'index-index'
##                        node_conn_dict[(n0, n1)] = (contyp, conn[contyp])
##                        node_conn_dict[(n1, n0)] = (contyp, conn[contyp])
##                    for col in v1.columns:
##                        # Catches index "atom", column "atom1"; does not catch atom10
##                        isrelated = False
##                        if name == col:
##                            isrelated = True
##                        elif isinstance(col, str) and (name == col[:-1] and col[-1].isdigit()):
##                            isrelated = True
##                        if isrelated == True:
##                            contyp = 'index-column'
##                            node_conn_dict[(n0, n1)] = (contyp, conn[contyp])
##                            node_conn_dict[(n1, n0)] = ('column-index', conn[contyp])
##        g = nx.Graph()
##        g.add_nodes_from(node_size_dict.keys())
##        g.add_edges_from(node_conn_dict.keys())
##        node_sizes = [node_size_dict[node] for node in g.nodes()]
##        node_labels = {node: ' {}\n({})'.format(node, node_class_name_dict[node]) for node in g.nodes()}
##        node_colors = [node_type_dict[node][1] for node in g.nodes()]
##        edge_colors = [node_conn_dict[edge][1] for edge in g.edges()]
##        # Build the figure and legends
##        if fig:
##            fig, ax = mpl.sns.plt.subplots(1, figsize=figsize)
##            ax.axis('off')
##            pos = nx.spring_layout(g)
##            f0 = nx.draw_networkx_nodes(g, pos=pos, ax=ax, alpha=0.7, node_size=node_sizes,
##                                        node_color=node_colors)
##            f1 = nx.draw_networkx_labels(g, pos=pos, labels=node_labels, font_size=16,
##                                         font_weight='bold', ax=ax)
##            f2 = nx.draw_networkx_edges(g, pos=pos, edge_color=edge_colors, width=2, ax=ax)
##            l1, ax = legend(set(node_conn_dict.values()), 'Connection', (1, 0), ax)
##            l2, ax = legend(set(node_type_dict.values()), 'Data Type', (1, 0.3), ax)
##            del f0, f1, f2, l2
##            fig.gca().add_artist(l1)
##        g.edge_types = {node: value[0] for node, value in node_conn_dict.items()}  # Attached connection information to network graph
##        return g
#=======
## -*- coding: utf-8 -*-
## Copyright (c) 2015-2017, Exa Analytics Development Team
## Distributed under the terms of the Apache License 2.0
#"""
#Container
#########################
#The :class:`~exa.core.container.Container` class is the primary object for
#data processing, analysis, and visualization. In brief, containers are composed
#of data objects whose contents are used for 2D and 3D visualization. Containers
#also provide some content management and data relationship features.
#
#See Also:
#    For a description of data objects see :mod:`~exa.core.numerical`.
#"""
#import os
#import numpy as np
#import pandas as pd
#import networkx as nx
#from sys import getsizeof
#from copy import deepcopy
#from .numerical import check_key
#from exa.util.utility import convert_bytes
#from exa.util import mpl
#import matplotlib.pyplot as plt
#
#
#class Container(object):
#    """
#    Container class responsible for all features related to data management.
#    """
#    _getter_prefix = 'compute'
#    _cardinal = None    # Name of the cardinal data table
#
#    def copy(self, name=None, description=None, meta=None):
#        """
#        Create a copy of the current object (may alter the container's name,
#        description, and update the metadata if needed).
#        """
#        cls = self.__class__
#        kwargs = self._rel(copy=True)
#        kwargs.update(self._data(copy=True))
#        if name is not None:
#            kwargs['name'] = name
#        if description is not None:
#            kwargs['description'] = description
#        if meta is not None:
#            kwargs['meta'] = meta
#        return cls(**kwargs)
#
#    def concat(self, *args, **kwargs):
#        """
#        Concatenate any number of container objects with the current object into
#        a single container object.
#
#        See Also:
#            For argument description, see :func:`~exa.core.container.concat`.
#        """
#        raise NotImplementedError()
#
#    def slice_naive(self, key):
#        """
#        Naively slice each data object in the container by the object's index.
#
#        Args:
#            key: Int, slice, or list by which to extra "sub"-container
#
#        Returns:
#            sub: Sub container of the same format with a view of the data
#
#        Warning:
#            To ensure that a new container is created, use the copy method.
#
#            .. code-block:: Python
#
#                mycontainer[slice].copy()
#        """
#        kwargs = {'name': self.name, 'description': self.description, 'meta': self.meta}
#        for name, data in self._data().items():
#            k = name[1:] if name.startswith('_') else name
#            kwargs[k] = data.slice_naive(key)
#        return self.__class__(**kwargs)
#
#    def slice_cardinal(self, key):
#        """
#        Slice the container according to its (primary) cardinal axis.
#
#        The "cardinal" axis can have any name so long as the name matches a
#        data object attached to the container. The index name for this object
#        should also match the value of the cardinal axis.
#
#        The algorithm builds a network graph representing the data relationships
#        (including information about the type of relationship) and then traverses
#        the edge tree (starting from the cardinal table). Each subsequent child
#        object in the tree is sliced based on its relationship with its parent.
#
#        Note:
#            Breadth first traversal is performed.
#
#        Warning:
#            This function does not make a copy (if possible): to ensure a new
#            object is created (a copy) use :func:`~exa.core.container.Container.copy`
#            after slicing.
#
#            .. code-block:: Python
#
#                myslice = mycontainer[::2].copy()
#
#        See Also:
#            For data network generation, see :func:`~exa.core.container.Container.network`.
#            For information about relationships between data objects see
#            :mod:`~exa.core.numerical`.
#        """
#        if self._cardinal:
#            cls = self.__class__
#            key = check_key(self[self._cardinal], key, cardinal=True)
#            g = self.network(fig=False)
#            kwargs = {self._cardinal: self[self._cardinal].ix[key], 'name': self.name,
#                      'description': self.description, 'meta': self.meta}
#            # Next traverse, breadth first, all data objects
#            for parent, child in nx.bfs_edges(g, self._cardinal):
#                if child in kwargs:
#                    continue
#                typ = g.edge_types[(parent, child)]
#                if self._cardinal in self[child].columns and hasattr(self[child], 'slice_cardinal'):
#                    kwargs[child] = self[child].slice_cardinal(key)
#                elif typ == 'index-index':
#                    # Select from the child on the parent's index (the parent is
#                    # in the kwargs already).
#                    kwargs[child] = self[child].ix[kwargs[parent].index.values]
#                elif typ == 'index-column':
#                    # Select from the child where the column (of the same name as
#                    # the parent) is in the parent's index values
#                    cdf = self[child]
#                    kwargs[child] = cdf[cdf[parent].isin(kwargs[parent].index.values)]
#                elif typ == 'column-index':
#                    # Select from the child where the child's index is in the
#                    # column of the parent. Note that this relationship
#                    cdf = self[child]
#                    cin = cdf.index.name
#                    cols = [col for col in kwargs[parent] if cin == col or (cin == col[:-1] and col[-1].isdigit())]
#                    index = kwargs[parent][cols].stack().astype(np.int64).values
#                    kwargs[child] = cdf[cdf.index.isin(index)]
#            return cls(**kwargs)
#
#    def cardinal_groupby(self):
#        """
#        Create an instance of this class for every step in the cardinal dimension.
#        """
#        if self._cardinal:
#            g = self.network(fig=False)
#            cardinal_indexes = self[self._cardinal].index.values
#            selfs = {}
#            cls = self.__class__
#            for cardinal_index in cardinal_indexes:
#                kwargs = {self._cardinal: self[self._cardinal].ix[[cardinal_index]]}
#                for parent, child in nx.bfs_edges(g):
#                    if child in kwargs:
#                        continue
#                    typ = g.edge_types[(parent, child)]
#                    if self._cardinal in self[child].columns and hasattr(self[child], 'slice_cardinal'):
#                        kwargs[child] = self[child].slice_cardinal(key)
#                    elif typ == 'index-index':
#                        # Select from the child on the parent's index (the parent is
#                        # in the kwargs already).
#                        kwargs[child] = self[child].ix[kwargs[parent].index.values]
#                    elif typ == 'index-column':
#                        # Select from the child where the column (of the same name as
#                        # the parent) is in the parent's index values
#                        cdf = self[child]
#                        kwargs[child] = cdf[cdf[parent].isin(kwargs[parent].index.values)]
#                    elif typ == 'column-index':
#                        # Select from the child where the child's index is in the
#                        # column of the parent. Note that this relationship
#                        cdf = self[child]
#                        cin = cdf.index.name
#                        cols = [col for col in kwargs[parent] if cin == col or (cin == col[:-1] and col[-1].isdigit())]
#                        index = kwargs[parent][cols].stack().astype(np.int64).values
#                        kwargs[child] = cdf[cdf.index.isin(index)]
#                selfs[cardinal_index] = cls(**kwargs)
#        return selfs
#
#    def info(self):
#        """
#        Display information about the container's data objects (note that info
#        on metadata and visualization objects is also provided).
#
#        Note:
#            Sizes are reported in bytes.
#        """
#        names = []
#        types = []
#        sizes = []
#        names.append('WIDGET')
#        types.append('-')
#        s = 0
#        sizes.append(s)
#        names.append('METADATA')
#        types.append('-')
#        s = 0
#        for obj in self._rel().values():
#            s += getsizeof(obj)
#        sizes.append(s)
#        for name, obj in self._data().items():
#            names.append(name[1:] if name.startswith('_') else name)
#            types.append('.'.join((obj.__module__, obj.__class__.__name__)))
#            if isinstance(obj, pd.Series):
#                sizes.append(obj.memory_usage())
#            else:
#                sizes.append(obj.memory_usage().sum())
#        inf = pd.DataFrame.from_dict({'object': names, 'type': types, 'size': sizes})
#        inf.set_index('object', inplace=True)
#        return inf.sort_index()
#
#    def memory_usage(self, string=False):
#        """
#        Get the memory usage estimate of the container.
#
#        Args:
#            string (bool): Human readable string (default false)
#
#        See Also:
#            :func:`~exa.core.container.Container.info`
#        """
#        if string:
#            n = getsizeof(self)
#            return ' '.join((str(s) for s in convert_bytes(n)))
#        return self.info()['size']
#
#    def network(self, figsize=(14, 9), fig=True):
#        """
#        Display information about the container's object relationships.
#
#        Nodes correspond to data objects. The size of the node corresponds
#        to the size of the table in memory. The color of the node corresponds
#        to its fundamental data type. Nodes are labeled by their container
#        name; class information is listed below. The color of the connections
#        correspond to the type of relationship; either an index of one table
#        corresponds to a column in another table or the two tables share an
#        index.
#
#        Args:
#            figsize (tuple): Tuple containing figure dimensions
#            fig (bool): Generate the figure (default true)
#
#        Returns:
#            graph: Network graph object containing data relationships
#        """
#        conn_types = ['index-index', 'index-column']
#        conn_colors = mpl.sns.color_palette('viridis', len(conn_types))
#        conn = dict(zip(conn_types, conn_colors))
#
#        def get_node_type_color(obj):
#            """Gets the color of a node based on the node's (sub)type."""
#            cols = mpl.sns.color_palette('viridis', len(conn_types))
#            for col in cols:
#                if isinstance(obj, (pd.DataFrame, pd.Series, pd.SparseSeries, pd.SparseDataFrame)):
#                    typ = type(obj)
#                    return '.'.join((typ.__module__, typ.__name__)), col
#            return 'other', 'gray'
#
#        def legend(items, name, loc, ax):
#            """Legend creation helper function."""
#            proxies = []
#            descriptions = []
#            for label, color in items:
#                if label == 'column-index':
#                    continue
#                if name == 'Data Type':
#                    line = mpl.sns.mpl.lines.Line2D([], [], linestyle='none', color=color, marker='o')
#                else:
#                    line = mpl.sns.mpl.lines.Line2D([], [], linestyle='-', color=color)
#                proxies.append(line)
#                descriptions.append(label)
#            lgnd = ax.legend(proxies, descriptions, title=name, loc=loc, frameon=True)
#            lgnd_frame = lgnd.get_frame()
#            lgnd_frame.set_facecolor('white')
#            lgnd_frame.set_edgecolor('black')
#            return lgnd, ax
#
#        info = self.info()
#        info = info[info['type'] != '-']
#        info['size'] *= 13000/info['size'].max()
#        info['size'] += 2000
#        node_size_dict = info['size'].to_dict()      # Can pull all nodes from keys
#        node_class_name_dict = info['type'].to_dict()
#        node_type_dict = {}    # Values are tuple of "underlying" type and color
#        node_conn_dict = {}    # Values are tuple of connection type and color
#        items = self._data().items()
#        for k0, v0 in items:
#            n0 = k0[1:] if k0.startswith('_') else k0
#            node_type_dict[n0] = get_node_type_color(v0)
#            for k1, v1 in items:
#                if v0 is v1:
#                    continue
#                n1 = k1[1:] if k1.startswith('_') else k1
#                for name in v0.index.names:    # Check the index of data object 0 against the index
#                    if name is None:           # and columns of data object 1
#                        continue
#                    if name in v1.index.names:
#                        contyp = 'index-index'
#                        node_conn_dict[(n0, n1)] = (contyp, conn[contyp])
#                        node_conn_dict[(n1, n0)] = (contyp, conn[contyp])
#                    for col in v1.columns:
#                        # Catches index "atom", column "atom1"; does not catch atom10
#                        if name == col or (name == col[:-1] and col[-1].isdigit()):
#                            contyp = 'index-column'
#                            node_conn_dict[(n0, n1)] = (contyp, conn[contyp])
#                            node_conn_dict[(n1, n0)] = ('column-index', conn[contyp])
#        g = nx.Graph()
#        g.add_nodes_from(node_size_dict.keys())
#        g.add_edges_from(node_conn_dict.keys())
#        node_sizes = [node_size_dict[node] for node in g.nodes()]
#        node_labels = {node: ' {}\n({})'.format(node, node_class_name_dict[node]) for node in g.nodes()}
#        node_colors = [node_type_dict[node][1] for node in g.nodes()]
#        edge_colors = [node_conn_dict[edge][1] for edge in g.edges()]
#        # Build the figure and legends
#        if fig:
#            fig, ax = plt.subplots(1, figsize=figsize)
#            ax.axis('off')
#            pos = nx.spring_layout(g)
#            f0 = nx.draw_networkx_nodes(g, pos=pos, ax=ax, alpha=0.7, node_size=node_sizes,
#                                        node_color=node_colors)
#            f1 = nx.draw_networkx_labels(g, pos=pos, labels=node_labels, font_size=17,
#                                         font_weight='bold', ax=ax)
#            f2 = nx.draw_networkx_edges(g, pos=pos, edge_color=edge_colors, width=2, ax=ax)
#            l1, ax = legend(set(node_conn_dict.values()), 'Connection', (1, 0), ax)
#            l2, ax = legend(set(node_type_dict.values()), 'Data Type', (1, 0.3), ax)
#            fig.gca().add_artist(l1)
#        g.edge_types = {node: value[0] for node, value in node_conn_dict.items()}  # Attached connection information to network graph
#        return g
#
#    def save(self, path):
#        """
#        Save the container as an HDF5 archive.
#
#        Args:
#            path (str): Path where to save the container
#        """
#        # First save the file record
#        with scoped_session() as session:
#            cfile = ContainerFile(name=self.name, description=self.description,
#                                  size=getsizeof(self))
#            session.add(cfile)
#        # Second save the data
#        if path is None:
#            path = self.hexuid + '.hdf5'
#        elif os.path.isdir(path):
#            path += os.sep + self.hexuid + '.hdf5'
#        elif not (path.endswith('.hdf5') or path.endswith('.hdf')):
#            raise ValueError('File path must have a ".hdf5" or ".hdf" extension.')
#        with pd.HDFStore(path, 'w') as store:
#            store['kwargs'] = pd.Series()
#            store.get_storer('kwargs').attrs.metadata = self._rel()
#            fc = 0    # Field counter (see special handling of fields below)
#            for name, data in self._data().items():
#                if hasattr(data, '_revert_categories'):
#                    data._revert_categories()
#                name = name[1:] if name.startswith('_') else name
#                if isinstance(data, Field):    # Fields are handled separately
#                    fname = 'FIELD{}_'.format(fc) + name + '/'
#                    store[fname + 'data'] = pd.DataFrame(data)
#                    for i, field in enumerate(data.field_values):
#                        ffname = fname + 'values' + str(i)
#                        if isinstance(field, pd.Series):
#                            store[ffname] = pd.Series(field)
#                        else:
#                            store[ffname] = pd.DataFrame(field)
#                    fc += 1
#                elif isinstance(data, Series):
#                    s = pd.Series(data)
#                    if isinstance(data.dtype, pd.types.dtypes.CategoricalDtype):
#                        s = s.astype('O')
#                    store[name] = s
#                elif isinstance(data, DataFrame):
#                    store[name] = pd.DataFrame(data)
#                elif isinstance(data, SparseSeries):
#                    s = pd.SparseSeries(data)
#                    if isinstance(data.dtype, pd.types.dtypes.CategoricalDtype):
#                        s = s.astype('O')
#                    store[name] = s
#                elif isinstance(data, SparseDataFrame):
#                    store[name] = pd.SparseDataFrame(data)
#                else:
#                    if hasattr(data, 'dtype') and isinstance(data.dtype, pd.types.dtypes.CategoricalDtype):
#                        data = data.astype('O')
#                    else:
#                        for col in data:
#                            if isinstance(data[col].dtype, pd.types.dtypes.CategoricalDtype):
#                                data[col] = data[col].astype('O')
#                    store[name] = data
#                if hasattr(data, '_set_categories'):
#                    data._set_categories()
#
#    @classmethod
#    def load(cls, pkid_or_path=None):
#        """
#        Load a container object from a persistent location or file path.
#
#        Args:
#            pkid_or_path: Integer pkid corresponding to the container table or file path
#
#        Returns:
#            container: The saved container object
#        """
#        path = pkid_or_path
#        if isinstance(path, (int, np.int32, np.int64)):
#            raise NotImplementedError('Lookup via CMS not implemented.')
#        elif not os.path.isfile(path):
#            raise FileNotFoundError('File {} not found.'.format(path))
#        kwargs = {}
#        with pd.HDFStore(path) as store:
#            for key in store.keys():
#                if 'kwargs' in key:
#                    kwargs.update(store.get_storer(key).attrs.metadata)
#                else:
#                    name = str(key[1:])
#                    kwargs[name] = store[key]
#        # Process any fields
#        n = [int(key.split('_')[0].replace('FIELD', '')) for key in kwargs.keys() if 'FIELD' in key]
#        if len(n) != 0:
#            n = max(n)
#            to_del = []
#            for i in range(n + 1):
#                search = 'FIELD' + str(i)
#                names = [key for key in kwargs.keys() if search in key]
#                to_del += names
#                arg = names[0].replace(search + '_', '').split('/')[0]
#                field_values = [kwargs[key] for key in names if 'values' in key]
#                dkey = None
#                for name in names:
#                    if 'data' in name:
#                        dkey = name
#                field_data = kwargs[dkey]
#                kwargs[arg] = field_data
#                kwargs[arg + '_values'] = field_values
#            for name in to_del:
#                del kwargs[name]
#        return cls(**kwargs)
#
#    def _rel(self, copy=False):
#        """
#        Get descriptive kwargs of the container (e.g. name, description, meta).
#        """
#        rel = {}
#        for key, obj in vars(self).items():
#            if not isinstance(obj, (pd.Series, pd.DataFrame, pd.SparseSeries, pd.SparseDataFrame)) and not key.startswith('_'):
#                if copy and 'id' not in key:
#                    rel[key] = deepcopy(obj)
#                else:
#                    rel[key] = obj
#        return rel
#
#    def _data(self, copy=False):
#        """
#        Get data kwargs of the container (i.e. dataframe and series objects).
#        """
#        data = {}
#        for key, obj in vars(self).items():
#            if isinstance(obj, (pd.Series, pd.DataFrame, pd.SparseSeries, pd.SparseDataFrame)):
#                if copy:
#                    data[key] = obj.copy(deep=True)
#                else:
#                    data[key] = obj
#        return data
#
#    def __delitem__(self, key):
#        if key in vars(self):
#            del self.__dict__[key]
#
#    def __sizeof__(self):
#        """Note that this function must return a Python integer."""
#        return int(self.info()['size'].sum())
#
#    def __getitem__(self, key):
#        if isinstance(key, str):
#            return getattr(self, key)
#        elif isinstance(key, (int, slice, list)) and self._cardinal is None:
#            return self.slice_naive(key)
#        elif isinstance(key, (int, slice, list)) and self._cardinal is not None:
#            return self.slice_cardinal(key)
#        raise KeyError()
#
#    def __init__(self, name=None, description=None, meta=None, **kwargs):
#        for key, value in kwargs.items():
#            setattr(self, key, value)
#        self.name = name
#        self.description = description
#        self.meta = meta
#
#
#class TypedMeta(type):
#    """
#    This metaclass creates statically typed class attributes using the property
#    framework.
#
#    .. code-block:: Python
#
#        class TestMeta(TypedMeta):
#            attr1 = (int, float)
#            attr2 = DataFrame
#
#        class TestClass(metaclass=TestMeta):
#            def __init__(self, attr1, attr2):
#                self.attr1 = attr1
#                self.attr2 = attr2
#
#    The above code dynamically creates code that looks like the following:
#
#    .. code-block:: Python
#
#        class TestClass:
#            @property
#            def attr1(self):
#                return self._attr1
#
#            @attr1.setter
#            def attr1(self, obj):
#                if not isinstance(obj, (int, float)):
#                    raise TypeError('attr1 must be int')
#                self._attr1 = obj
#
#            @attr1.deleter
#            def attr1(self):
#                del self._attr1
#
#            @property
#            def attr2(self):
#                return self._attr2
#
#            @attr2.setter
#            def attr2(self, obj):
#                if not isinstance(obj, DataFrame):
#                    raise TypeError('attr2 must be DataFrame')
#                self._attr2 = obj
#
#            @attr2.deleter
#            def attr2(self):
#                del self._attr2
#
#            def __init__(self, attr1, attr2):
#                self.attr1 = attr1
#                self.attr2 = attr2
#    """
#    @staticmethod
#    def create_property(name, ptype):
#        """
#        Creates a custom property with a getter that performs computing
#        functionality (if available) and raise a type error if setting
#        with the wrong type.
#
#        Note:
#            By default, the setter attempts to convert the object to the
#            correct type; a type error is raised if this fails.
#        """
#        pname = '_' + name
#        def getter(self):
#            # This will be where the data is store (e.g. self._name)
#            # This is the default property "getter" for container data objects.
#            # If the property value is None, this function will check for a
#            # convenience method with the signature, self.compute_name() and call
#            # it prior to returning the property value.
#            if not hasattr(self, pname) and hasattr(self, '{}{}'.format(self._getter_prefix, pname)):
#                self['{}{}'.format(self._getter_prefix, pname)]()
#            if not hasattr(self, pname):
#                raise AttributeError('Please compute or set {} first.'.format(name))
#            return getattr(self, pname)
#
#        def setter(self, obj):
#            # This is the default property "setter" for container data objects.
#            # Prior to setting a property value, this function checks that the
#            # object's type is correct.
#            if not isinstance(obj, ptype):
#                try:
#                    obj = ptype(obj)
#                except Exception:
#                    raise TypeError('Must be able to convert object {0} to {1} (or must be of type {1})'.format(name, ptype))
#            setattr(self, pname, obj)
#
#        def deleter(self):
#            # Deletes the property's value.
#            del self[pname]
#
#        return property(getter, setter, deleter)
#
#    def __new__(metacls, name, bases, clsdict):
#        """
#        Modification of the class definition occurs here; we iterate over all
#        statically typed attributes and attach their property (see
#        :func:`~exa.container.TypedMeta.create_property`) definition, returning
#        the new class definition.
#        """
#        for k, v in vars(metacls).items():
#            if isinstance(v, type) and not k.startswith('_'):
#                clsdict[k] = metacls.create_property(k, v)
#        return super(TypedMeta, metacls).__new__(metacls, name, bases, clsdict)
#>>>>>>> 0.3.9

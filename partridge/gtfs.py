try:
    from functools import lru_cache
except ImportError:
    from functools32 import lru_cache

import io
import networkx as nx
import numpy as np
import os
import pandas as pd
from zipfile import ZipFile


from partridge.config import default_config, empty_config
from partridge.utilities import empty_df, setwrap


def read_file(filename):
    return property(lambda feed: get_dataframe(feed, filename))


@lru_cache(maxsize=None)
def get_dataframe(feed, filename):
    # Get config for node
    config = feed.config

    if config.has_node(filename):
        node = config.nodes[filename]
    else:
        node = {}

    columns = node.get('required_columns', [])
    converters = node.get('converters', {})

    # If the file isn't in the zip, return an empty DataFrame.
    if filename not in feed.zmap:
        return empty_df(columns)

    # Read CSV into DataFrame, prune it according to the dependency graph
    with ZipFile(feed.path) as zipreader:
        # Prepare a chunked reader for the file
        zfile = zipreader.open(feed.zmap[filename], 'r')
        iowrapper = io.TextIOWrapper(zfile, encoding='utf-8-sig')
        reader = pd.read_csv(iowrapper,
                             chunksize=10000,
                             dtype=np.unicode,
                             index_col=False,
                             low_memory=False,
                             skipinitialspace=True,
                             )

        # Gather the dependencies between this file and others
        file_dependencies = {
            # property name : dependencies
            os.path.splitext(depfile)[0]: data['dependencies'].items()
            for _, depfile, data in config.out_edges(filename, data=True)
            if 'dependencies' in data
        }

        # Gather applicable view filter params
        view_filters = {
            # column name : set of strings
            col: set(map(np.unicode, setwrap(values)))
            for col, values in feed.view.get(filename, {}).items()
        }

        # Process the file in chunks
        chunks = []
        for i, chunk in enumerate(reader):
            # Cleanup column names just to be safe
            chunk = chunk.rename(columns=lambda x: x.strip())

            if i == 0:
                # Track the actual columns in the file if present
                columns = list(chunk.columns)

            # Apply view filters
            for col, values in view_filters.items():
                # If applicable, filter this chunk by the
                # given set of values
                if col in chunk.columns:
                    chunk = chunk[chunk[col].isin(values)]

            # Prune the chunk
            for propname, dependencies in file_dependencies.items():
                # Read the filtered, pruned, and cached file dependency
                depdf = getattr(feed, propname)

                for col, depcol in dependencies:
                    # If applicable, prune this chunk by the other
                    if col in chunk.columns and depcol in depdf.columns:
                        chunk = chunk[chunk[col].isin(depdf[depcol])]

            # Discard entirely filtered/pruned chunks
            if not chunk.empty:
                chunks.append(chunk)

        # If all chunks were completely filtered/pruned away,
        # return an empty DataFrame.
        if len(chunks) == 0:
            return empty_df(columns)

    # Concatenate chunks into one DataFrame
    df = pd.concat(chunks)

    # Apply type conversions, strip leading/trailing whitespace
    for col in df.columns:
        if df[col].any():
            df[col] = df[col].str.strip()
            if col in converters:
                vfunc = converters[col]
                df[col] = vfunc(df[col])

    return df


class feed(object):
    def __init__(self, path, config=None, view=None):
        self.path = path
        self.config = default_config() if config is None else config
        self.view = {} if view is None else view
        self.zmap = {}

        assert os.path.isfile(self.path), \
            'File not found: {}'.format(self.path)

        assert nx.is_directed_acyclic_graph(self.config), \
            'Config must be a DAG'

        roots = {n for n, d in self.config.out_degree() if d == 0}
        for filename, param in self.view.items():
            assert filename in roots, \
                'Filter param given for a non-root node ' \
                'of the config graph: {} {}'.format(filename, param)

        with ZipFile(self.path) as zipreader:
            for zpath in zipreader.namelist():
                basename = os.path.basename(zpath)
                if zpath.endswith('.txt'):
                    assert basename not in self.zmap, \
                        'More than one {} in zip'.format(basename)
                self.zmap[basename] = zpath

    agency = read_file('agency.txt')
    calendar = read_file('calendar.txt')
    calendar_dates = read_file('calendar_dates.txt')
    fare_attributes = read_file('fare_attributes.txt')
    fare_rules = read_file('fare_rules.txt')
    feed_info = read_file('feed_info.txt')
    frequencies = read_file('frequencies.txt')
    routes = read_file('routes.txt')
    shapes = read_file('shapes.txt')
    stops = read_file('stops.txt')
    stop_times = read_file('stop_times.txt')
    transfers = read_file('transfers.txt')
    trips = read_file('trips.txt')


# No pruning or type coercion
class raw_feed(feed):
    def __init__(self, path):
        super(raw_feed, self).__init__(path, config=empty_config())

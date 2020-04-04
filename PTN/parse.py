#!/usr/bin/env python
# -*- coding: utf-8 -*-

import re
from .patterns import patterns, types, bt_sites


class PTN(object):
    def _escape_regex(self, string):
        return re.sub('[\-\[\]{}()*+?.,\\\^$|#\s]', '\\$&', string)

    def __init__(self):
        self.torrent = None
        self.excess_raw = None
        self.group_raw = None
        self.start = None
        self.end = None
        self.title_raw = None
        self.parts = None

    def _part(self, name, match, raw, clean):
        # The main core instructuions
        self.parts[name] = clean

        if len(match) != 0:
            # The instructions for extracting title
            index = self.torrent['name'].find(match[0])
            if index == 0:
                self.start = len(match[0])
            elif self.end is None or index < self.end:
                self.end = index

        if name != 'excess':
            # The instructions for adding excess
            if name == 'group':
                self.group_raw = raw
            if raw is not None:
                self.excess_raw = self.excess_raw.replace(raw, '')

    def _late(self, name, clean):
        if name == 'group':
            self._part(name, [], None, clean)
        elif name == 'episodeName':
            clean = re.sub('[\._]', ' ', clean)
            clean = re.sub('_+$', '', clean)
            self._part(name, [], None, clean.strip())

    def parse(self, name):
        self.parts = {}
        self.torrent = {'name': name}
        self.excess_raw = name
        self.group_raw = ''
        self.start = 0
        self.end = None
        self.title_raw = None

        for key, pattern in patterns:
            if key not in ('season', 'episode', 'website'):
                pattern = r'\b%s\b' % pattern

            clean_name = re.sub('_', ' ', self.torrent['name'])
            match = re.findall(pattern, clean_name, re.IGNORECASE)
            if len(match) == 0:
                continue

            index = {}

            # With multiple matches, we will usually want to use the first match.
            # For 'year', we instead use the last instance of a year match since,
            # if a title includes a year, we don't want to use this for the year field.
            match_index = 0
            if key == 'year':
                match_index = -1

            if isinstance(match[match_index], tuple):
                match = list(match[match_index])
            if len(match) > 1:
                index['raw'] = 0
                index['clean'] = 0
                # for season we might have it in index 1 or index 2
                # i.e. "5x09"
                for i in range(1, len(match)):
                    if match[i]:
                        index['clean'] = i
                        break
            else:
                index['raw'] = 0
                index['clean'] = 0

            if key == 'season' and index['clean'] == 0:
                # handle multi season
                # i.e. S01-S09
                m = re.findall('s([0-9]{2})-s([0-9]{2})', clean_name, re.IGNORECASE)
                if m:
                    clean = list(range(int(m[0][0]), int(m[0][1])+1))
            elif key == 'language' and len(match) > 1:
                clean = match
            elif key in types.keys() and types[key] == 'boolean':
                clean = True
            else:
                clean = match[index['clean']]
                if key in types.keys() and types[key] == 'integer':
                    clean = int(clean)

            if key == 'group':
                if (re.search(patterns[5][1], clean, re.IGNORECASE) or
                    re.search(patterns[4][1], clean)):
                    continue  # Codec and quality.
                if re.match('[^ ]+ [^ ]+ .+', clean):
                    key = 'episodeName'
            if key == 'episode':
                sub_pattern = self._escape_regex(match[index['raw']])
                self.torrent['map'] = re.sub(
                    sub_pattern, '{episode}', self.torrent['name']
                )

            self._part(key, match, match[index['raw']], clean)

        # Start process for title
        raw = self.torrent['name']
        if self.end is not None:
            raw = raw[self.start:self.end].split('(')[0]

        clean = re.sub('^ -', '', raw)
        if clean.find(' ') == -1 and clean.find('.') != -1:
            clean = re.sub('\.', ' ', clean)
        clean = re.sub('_', ' ', clean)
        clean = re.sub('([\[\(_]|- )$', '', clean).strip()
        clean = clean.strip(' _-')

        self._part('title', [], raw, clean)

        # Start process for end
        clean = re.sub('(^[-\. ()]+)|([-\. ]+$)', '', self.excess_raw)
        clean = re.sub('[\(\)\/]', ' ', clean)
        match = re.split('\.\.+| +', clean)
        if len(match) > 0 and isinstance(match[0], tuple):
            match = list(match[0])

        clean = filter(bool, match)
        clean = [item for item in filter(lambda a: a != '-', clean)]
        clean = [item.strip('-') for item in clean]
        if len(clean) != 0:
            group_pattern = clean[-1] + self.group_raw
            if self.torrent['name'].find(group_pattern) == \
                    len(self.torrent['name']) - len(group_pattern):
                self._late('group', clean.pop() + self.group_raw)

            if 'map' in self.torrent.keys() and len(clean) != 0:
                episode_name_pattern = (
                    '{episode}'
                    '' + re.sub('_+$', '', clean[0])
                )
                if self.torrent['map'].find(episode_name_pattern) != -1:
                    self._late('episodeName', clean.pop(0))

        # clean group name from having a container name
        if 'group' in self.parts and 'container' in self.parts:
            group = self.parts['group']
            container = self.parts['container']
            if group.lower().endswith('.'+container.lower()):
                group = group[:-(len(container)+1)]
                self.parts['group'] = group

        # clean group name from having bt site name
        if 'group' in self.parts:

            group = self.parts['group']
            sites = '|'.join(bt_sites)
            pat = '\[(' + sites + ')\]$'
            group = re.sub(pat, '', group, flags=re.IGNORECASE)
            if group:
                self.parts['group'] = group

        if len(clean) != 0:
            if len(clean) == 1:
                clean = clean[0]
            self._part('excess', [], self.excess_raw, clean)
        return self.parts

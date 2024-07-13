#!/usr/bin/env python
import re
from typing import Dict, List, Tuple, Union, Optional, Any
from .extras import (
    delimiters,
    langs,
    genres,
    exceptions,
    complete_series,
    patterns_ignore_title,
    get_channel_audio_options,
    prefix_pattern_with,
    suffix_pattern_with,
    link_patterns,
)
from .patterns import patterns, patterns_ordered, types, patterns_allow_overlap
from .post import post_processing_after_excess, post_processing_before_excess


class PTN:
    def __init__(self):
        self.torrent_name = None
        self.parts: Dict[str, Union[str, int, List[int], bool]] = {}
        self.part_slices: Dict[str, Tuple[int, int]] = {}
        self.match_slices: List[Tuple[int, int]] = []
        self.standardise = False
        self.coherent_types = False
        self.post_title_pattern = self._generate_post_title_pattern()

    def _generate_post_title_pattern(self) -> str:
        return f"(?:{link_patterns(patterns['seasons'])}|{link_patterns(patterns['year'])}|720p|1080p)"

    def _part(self, name: str, match_slice: Optional[Tuple[int, int]], clean: Union[str, int, List[int], bool], overwrite: bool = False) -> None:
        if overwrite or name not in self.parts:
            if self.coherent_types and name not in ["title", "episodeName"] and not isinstance(clean, bool):
                if not isinstance(clean, list):
                    clean = [clean]
            self.parts[name] = clean
            self.part_slices[name] = match_slice

        # Ignored patterns will still be considered 'matched' to remove them from excess.
        if match_slice:
            self.match_slices.append(match_slice)

    @staticmethod
    def _clean_dots(string: str) -> str:
        if ' ' not in string and '.' in string:
            string = re.sub(r"\.{4,}", "... ", string)

            # Replace any instances of less than 3 dots with a space
            # Lookarounds are used to prevent the 3-dots (ellipses) from being replaced
            string = re.sub(r"(?<!\.)\.\.(?!\.)", " ", string)
            string = re.sub(r"(?<!\.)\.(?!\.\.)", " ", string)
        return string

    def _clean_string(self, string: str) -> str:
        clean = re.sub(r"^( -|\(|\[)", "", string)
        clean = self._clean_dots(clean)

        clean = re.sub(r"_", " ", clean)
        clean = re.sub(r"([\[)_\]]|- )$", "", clean).strip()
        clean = clean.strip(" _-")

        # Again, we need to clean up the dots & strip for non-english chars titles that get cleaned from above re.sub.
        clean = self._clean_dots(clean).strip()
        return clean

    def parse(self, name: str, standardise: bool, coherent_types: bool) -> Dict[str, Union[str, int, List[int], bool]]:
        self.torrent_name = name.strip()
        self.parts = {}
        self.part_slices = {}
        self.match_slices = []
        self.standardise = standardise
        self.coherent_types = coherent_types

        for key in patterns_ordered:
            pattern_options = self.normalise_pattern_options(patterns[key])
            self._apply_patterns(key, pattern_options)

        self.process_title()
        self.fix_known_exceptions()

        unmatched = self.get_unmatched()
        for f in post_processing_before_excess:
            unmatched = f(self, unmatched)

        cleaned_unmatched = self.clean_unmatched()
        if cleaned_unmatched:
            self._part("excess", None, cleaned_unmatched)

        for f in post_processing_after_excess:
            f(self)

        return self.parts

    def _apply_patterns(self, key: str, pattern_options: List[Tuple[str, Optional[str], Optional[Union[str, List[Tuple[str, List[Any]]]]]]]) -> None:
        for pattern, replace, transforms in pattern_options:
            if key not in ("seasons", "episodes", "site", "languages", "genres"):
                pattern = rf"\b(?:{pattern})\b"

            clean_name = re.sub(r"_", " ", self.torrent_name)
            matches = self.get_matches(pattern, clean_name, key)

            if not matches:
                continue

            # With multiple matches, we will usually want to use the first match.
            # For 'year', we instead use the last instance of a year match since,
            # if a title includes a year, we don't want to use this for the year field.
            match_index = -1 if key == "year" else 0
            match = matches[match_index]["match"]
            match_start, match_end = matches[match_index]["start"], matches[match_index]["end"]

            if key in self.parts:  # We can skip ahead if we already have a matched part
                self._part(key, (match_start, match_end), None, overwrite=False)
                continue

            index = self.get_match_indexes(match)

            if key in ("seasons", "episodes"):
                clean = self.get_season_episode(match)
            elif key == "subtitles":
                clean = self.get_subtitles(match)
            elif key in ("languages", "genres"):
                clean = self.split_multi(match)
            elif key in types and types[key] == "boolean":
                clean = True
            else:
                clean = match[index["clean"]]
                if key in types and types[key] == "integer":
                    clean = int(clean)

            if self.standardise:
                clean = self.standardise_clean(clean, key, replace, transforms)

            part_overlaps = any(
                self._is_overlap(part_slice, (match_start, match_end))
                for part, part_slice in self.part_slices.items()
                if part not in patterns_allow_overlap
            )

            if not part_overlaps:
                self._part(key, (match_start, match_end), clean)

    # Handles all the optional/missing tuple elements into a consistent list.
    @staticmethod
    def normalise_pattern_options(pattern_options: Union[str, Tuple, List[Union[str, Tuple]]]) -> List[Tuple[str, Optional[str], Optional[Union[str, List[Tuple[str, List[Any]]]]]]]:
        pattern_options_norm = []

        if isinstance(pattern_options, tuple):
            pattern_options = [pattern_options]
        elif not isinstance(pattern_options, list):
            pattern_options = [(pattern_options, None, None)]
        for options in pattern_options:
            if len(options) == 2:  # No transformation
                pattern_options_norm.append(options + (None,))
            elif isinstance(options, tuple):
                if isinstance(options[2], tuple):
                    pattern_options_norm.append(
                        tuple(list(options[:2]) + [[options[2]]])
                    )
                elif isinstance(options[2], list):
                    pattern_options_norm.append(options)
                else:
                    pattern_options_norm.append(
                        tuple(list(options[:2]) + [[(options[2], [])]])
                    )

            else:
                pattern_options_norm.append((options, None, None))
        return pattern_options_norm

    def get_matches(self, pattern: str, clean_name: str, key: str) -> List[Dict[str, Union[str, int]]]:
        grouped_matches = []
        matches = list(re.finditer(pattern, clean_name, re.IGNORECASE))
        for m in matches:
            if m.start() < self.ignore_before_index(clean_name, key):
                continue
            groups = m.groups()
            if not groups:
                grouped_matches.append((m.group(), m.start(), m.end()))
            else:
                grouped_matches.append((groups, m.start(), m.end()))

        parsed_matches = []
        for match in grouped_matches:
            m = match[0]
            if isinstance(m, tuple):
                m = list(m)
            else:
                m = [m]
            parsed_matches.append({"match": m, "start": match[1], "end": match[2]})
        return parsed_matches

    # Only use part of the torrent name after the (guessed) title (split at a season or year)
    # to avoid matching certain patterns that could show up in a release title.
    def ignore_before_index(self, clean_name: str, key: str) -> int:
        match = None
        if key in patterns_ignore_title:
            patterns_ignored = patterns_ignore_title[key]
            if not patterns_ignored:
                match = re.search(self.post_title_pattern, clean_name, re.IGNORECASE)
            else:
                for ignore_pattern in patterns_ignored:
                    if re.findall(ignore_pattern, clean_name, re.IGNORECASE):
                        match = re.search(self.post_title_pattern, clean_name, re.IGNORECASE)

        if match:
            return match.start()
        return 0

    @staticmethod
    def get_match_indexes(match: List[str]) -> Dict[str, int]:
        index = {"raw": 0, "clean": 0}

        if len(match) > 1:
            # for season we might have it in index 1 or index 2
            # e.g. "5x09" TODO is this weirdness necessary
            for i in range(1, len(match)):
                if match[i]:
                    index["clean"] = i
                    break

        return index

    @staticmethod
    def get_season_episode(match: List[str]) -> Optional[List[int]]:
        m = re.findall(r"[0-9]+", match[0])
        if m and len(m) > 1:
            return list(range(int(m[0]), int(m[-1]) + 1))
        elif len(match) > 1 and match[1] and m:
            return list(range(int(m[0]), int(match[1]) + 1))
        elif m:
            return [int(m[0])]
        return None

    @staticmethod
    def split_multi(match: List[str]) -> List[str]:
        m = re.split(rf"{delimiters}+", match[0])
        return list(filter(None, m))

    @staticmethod
    def get_subtitles(match: List[str]) -> List[str]:
        # handle multi subtitles
        m = re.split(rf"{delimiters}+", match[0])
        m = list(filter(None, m))
        clean = []
        # If it's only 1 result, it's fine if it's just 'subs'.
        if len(m) == 1:
            clean = m
        else:
            for x in m:
                if not re.match("subs?|soft", x, re.I):
                    clean.append(x)
        return clean

    def standardise_clean(self, clean: Union[str, List[str]], key: str, replace: Optional[str], transforms: Optional[Union[str, List[Tuple[str, List[Any]]]]]) -> Union[str, List[str]]:
        if replace:
            clean = replace
        if transforms:
            for transform in filter(lambda t: t[0], transforms):
                clean = getattr(clean, transform[0])(*transform[1])
        if key in ["languages", "subtitles"]:
            clean = self.standardise_languages(clean)
            if not clean:
                clean = "Available"
        if key == "genres":
            clean = self.standardise_genres(clean)
        return clean

    @staticmethod
    def standardise_languages(clean: List[str]) -> List[str]:
        cleaned_langs = []
        for lang in clean:
            for lang_regex, lang_clean in langs:
                if re.match(lang_regex, re.sub(link_patterns(patterns["subtitles"][-2:]), "", lang, flags=re.I), re.IGNORECASE):
                    cleaned_langs.append(lang_clean)
                    break
        return cleaned_langs

    @staticmethod
    def standardise_genres(clean: List[str]) -> List[str]:
        standard_genres = []
        for genre in clean:
            for regex, clean in genres:
                if re.match(regex, genre, re.IGNORECASE):
                    standard_genres.append(clean)
                    break
        return standard_genres

    # Merge all the match slices (such as when they overlap), then remove
    # them from excess.
    def merge_match_slices(self) -> None:
        matches = sorted(self.match_slices, key=lambda match: match[0])
        slices = []
        i = 0
        while i < len(matches):
            start, end = matches[i]
            i += 1
            for next_start, next_end in matches[i:]:
                if next_start <= end:
                    end = max(end, next_end)
                    i += 1
                else:
                    break
            slices.append((start, end))
        self.match_slices = slices

    def process_title(self) -> None:
        unmatched = self.unmatched_list(keep_punctuation=False)

        # Use the first one as the title
        if unmatched:
            title_start, title_end = unmatched[0][0], unmatched[0][1]

            # If our unmatched is after the first 3 matches, we assume the title is missing
            # (or more likely got parsed as something else), as no torrents have it that
            # far away from the beginning of the release title.
            if len(self.part_slices) > 3 and title_start > sorted(self.part_slices.values(), key=lambda s: s[0])[3][0]:
                self._part("title", None, "")

            raw = self.torrent_name[title_start:title_end]
            # Something in square brackets with 3 chars or fewer is too weird to be right.
            # If this seems too arbitrary, make it any square bracket, and Mother test
            # case will lose its translated title (which is mostly fine I think).
            m = re.search(r"\(|(?:\[(?:.{,3}\]|[^\]]*\d[^\]]*\]?))", raw, flags=re.I)
            if m:
                relative_title_end = m.start()
                raw = raw[:relative_title_end]
                title_end = relative_title_end + title_start
            # Similar logic as above, but looking at beginning of string unmatched brackets.
            m = re.search(r"^(?:\)|\[.*\])", raw)
            if m:
                relative_title_start = m.end()
                raw = raw[relative_title_start:]
                title_start = relative_title_start + title_start
            clean = self._clean_string(self.clean_title(raw))
            # Re-add title_start to unrelative the index from raw to self.torrent_name
            self._part("title", (title_start, title_end), clean)
        else:
            self._part("title", None, "")

    def unmatched_list(self, keep_punctuation: bool = True) -> List[Tuple[int, int]]:
        self.merge_match_slices()
        unmatched = []
        prev_start = 0
        # A default so the last append won't crash if nothing has matched
        end = len(self.torrent_name)
        # Find all unmatched strings that aren't just punctuation
        for start, end in self.match_slices:
            if keep_punctuation or not re.match(rf"{delimiters}*\Z", self.torrent_name[prev_start:start]):
                unmatched.append((prev_start, start))
            prev_start = end

        # Add the last unmatched slice
        if keep_punctuation or not re.match(rf"{delimiters}*\Z", self.torrent_name[end:]):
            unmatched.append((end, len(self.torrent_name)))

        # If nothing matched, assume the whole thing is the title
        if not self.match_slices:
            unmatched.append((0, len(self.torrent_name)))
        return unmatched

    def fix_known_exceptions(self) -> None:
        # Considerations for results that are known to cause issues, such
        # as media with years in them but without a release year.
        for exception in exceptions:
            incorrect_key, incorrect_value = exception["incorrect_parse"]
            if self.parts["title"] == exception["parsed_title"] and incorrect_key in self.parts:
                if self.parts[incorrect_key] == incorrect_value or (self.coherent_types and incorrect_value in self.parts[incorrect_key]):
                    self.parts.pop(incorrect_key)
                    self._part("title", None, exception["actual_title"], overwrite=True)

    def get_unmatched(self) -> str:
        unmatched = ""
        for start, end in self.unmatched_list():
            unmatched += self.torrent_name[start:end]
        return unmatched

    def clean_unmatched(self) -> List[str]:
        unmatched = []
        for start, end in self.unmatched_list():
            unmatched.append(self.torrent_name[start:end])

        unmatched_clean = []
        for raw in unmatched:
            clean = re.sub(r"(^[-_.\s(),]+)|([-.\s,]+$)", "", raw)
            clean = re.sub(r"[()/]", " ", clean)
            unmatched_clean += re.split(r"\.\.+|\s+", clean)

        filtered = []
        for extra in unmatched_clean:
            # re.fullmatch() is not available in python 2.7, so we manually do it with \Z.
            if not re.match(rf"(?:Complete|Season|Full)?[\]\[,.+\- ]*(?:Complete|Season|Full)?\Z", extra, re.IGNORECASE):
                filtered.append(extra)
        return filtered

    @staticmethod
    def clean_title(raw_title: str) -> str:
        cleaned_title = raw_title.replace(r"[[(]movie[)\]]", "")  # clear movie indication flag
        cleaned_title = re.sub(patterns["RUSSIAN_CAST_REGEX"], " ", cleaned_title)  # clear russian cast information
        cleaned_title = re.sub(patterns["RELEASE_GROUP_REGEX_START"], r"\1", cleaned_title)  # remove release group markings sections from the start
        cleaned_title = re.sub(patterns["RELEASE_GROUP_REGEX_END"], r"\1", cleaned_title)  # remove unneeded markings section at the end if present
        cleaned_title = re.sub(patterns["ALT_TITLES_REGEX"], "", cleaned_title)  # remove alt language titles
        cleaned_title = re.sub(patterns["NOT_ONLY_NON_ENGLISH_REGEX"], "", cleaned_title)  # remove non english chars if they are not the only ones left
        return cleaned_title

    @staticmethod
    def _is_overlap(part_slice: Tuple[int, int], match_slice: Tuple[int, int]) -> bool:
        # Strict smaller/larger than since punctuation can overlap.
        return (part_slice[0] < match_slice[0] < part_slice[1]) or (part_slice[0] < match_slice[1] < part_slice[1])

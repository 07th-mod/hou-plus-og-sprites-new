import json
import os
import pathlib
import hashlib
import pickle
from pathlib import Path
import shutil
import re
import csv
import subprocess
from typing import List

# User imports
import path_util
import common
from common import CallData, ModToOGMatch, VoiceBasedMatch, VoiceMatchDatabase
import voice_util
import graphics_identifier


class GlobalResult:
    def __init__(self) -> None:
        self.missing_char_detected = False

class Statistics:
    def __init__(self):
        self.match_ok = 0
        self.match_fail = 0
        self.count_statistics = {} #type: dict[str, dict[str, list[CallData]]]
        self.missing_character_details = [] #type: list[str]
        # Keep track of mod bg path -> dict[og path, ogmatch?] guesses
        self.bg_guesses = {} #type: dict[str, dict[str, list[CallData]]]
        # Keep track of mod sprite path -> list[og path] guesses
        self.sprite_guesses = {} #type: dict[str, dict[str, list[CallData]]]

    def total(self):
        return self.match_ok + self.match_fail

    def add_match(self, mod_call_data: CallData, og_match: ModToOGMatch):
        if og_match.og_calldata is None:
            og_name = Path(og_match.og_path).stem
        else:
            og_name = og_match.og_calldata.name

        print(f"{mod_call_data.path} -> {og_name}")

        if mod_call_data.path not in self.count_statistics:
            self.count_statistics[mod_call_data.path] = {}

        mod_dict = self.count_statistics[mod_call_data.path]

        if og_name not in mod_dict:
            mod_dict[og_name] = []

        mod_dict[og_name].append(og_match)

    # TODO:
    # Then load in another script and determine final mapping?
    # Also need to scan every possible graphics path in modded game to make sure all paths are covered
    def save_as_json(self, output_file_path, output_missing_characters_path, global_result: GlobalResult):
        to_dump = {}

        for mod_path, og_results in self.count_statistics.items():
            to_dump[mod_path] = {}
            mod_path_dict = to_dump[mod_path]
            for og_path, og_list in og_results.items():
                mod_path_dict[og_path] = len(og_list)

        json_string = json.dumps(to_dump, sort_keys=True, indent=4)
        print(json_string)

        with open(output_file_path, 'w', encoding='utf-8') as f:
            f.write(json_string)

        missing_chars_path = Path(output_missing_characters_path)
        if missing_chars_path.exists():
            missing_chars_path.unlink()

        if self.missing_character_details:
            global_result.missing_char_detected = True

            summary = {}
            for (key, debug_out) in self.missing_character_details:
                summary[key] = debug_out

            with open(missing_chars_path, 'w', encoding='utf-8') as f:
                for key, value in summary.items():
                    f.write(f"Missing key: {key}\n")
                    f.write(f"{value}\n")

                f.write('\n\n\n----------------------------------------------------\n\n\n')
                for (key, debug_out) in self.missing_character_details:
                    f.writelines(debug_out)

        # Save bg and sprite guesses
        bg_guess_path = Path(output_file_path).with_suffix('.bg.txt')
        sprite_guess_path = Path(output_file_path).with_suffix('.sprite.txt')
        Statistics.save_matches(bg_guess_path, self.bg_guesses)
        Statistics.save_matches(sprite_guess_path, self.sprite_guesses)

    def add_missing_character(self, mod_matching_key_with_error: str, missing_character_line: str, og_lines: list[str]):
        out_string = f'{mod_matching_key_with_error}: {missing_character_line.strip()}\n'
        for line in og_lines:
            out_string += f'\t{line.strip()}\n'

        self.missing_character_details.append((mod_matching_key_with_error, out_string))

    def record_guesses(self, mod: CallData, og_calls: list[CallData]):
        if mod.is_sprite:
            for og_call in og_calls:
                if og_call.is_sprite or og_call.path.startswith('effect/'):
                    Statistics.add_guess(self.sprite_guesses, mod, og_call)
        else:
            for og_call in og_calls:
                if not og_call.is_sprite or og_call.path.startswith('effect/'):
                    Statistics.add_guess(self.bg_guesses, mod, og_call)

    @staticmethod
    def add_guess(guesses: dict[str, dict[str, list[CallData]]], mod: CallData, call: CallData):
        if mod.path not in guesses:
            guesses[mod.path] = {}

        mod_call_dict = guesses[mod.path] #type: dict[str, list[CallData]]

        if call.path not in mod_call_dict:
            mod_call_dict[call.path] = []

        mod_call_dict[call.path].append(call)

    @staticmethod
    def save_matches(out_file: str, guesses: dict[str, dict[str, list[CallData]]]):
        out_path = Path(out_file)
        all_guesses = guesses.items()

        # Remove the output file if no guesses found
        if not all_guesses:
            if out_path.exists():
                out_path.unlink()
            return

        # Otherwise write the guesses in human readable format
        with open(out_path, 'w', encoding='utf-8') as f:
            for mod_path, og_guesses in all_guesses:
                og_guess_paths = ','.join(og_guesses.keys())
                f.write(f'{mod_path}: [{og_guess_paths}]\n')


# assume outputLineAll is always a dummy (sometimes it's not, but this simplification should be OK)
outputLineAll = re.compile(r"OutputLineAll\([^;]*;")

outputLineRegex = re.compile(r"OutputLine\(\s*[^,]*,\s*\"(.*)\"\s*,")

ogSpritePathCharacterNameRegex = re.compile(r'"sprites/([^/]+)')

modEffectPathRegex = re.compile(r'"effect/(\w*)')

textRegex = re.compile(r'(effect/wagabu)|(effect/omo1)|(effect/omo2)|(effect/tyuui)|(effect/day_)')


def reverse_dict(d: dict[str, str]) -> dict[str, str]:
    reversed = {}

    for key, value in d.items():
        reversed[value] = key

    return reversed


def get_vanilla_only(log_lines):

    diff_lines = []

    got_commit = False
    got_file_start = False

    for line in log_lines:
        if got_commit:
            # if start a new commit then finished vanilla commit
            if line.startswith('commit'):
                break

            if got_file_start:
                if line.startswith('@@'):
                    # Ignore for now
                    pass
                else:
                    diff_lines.append(line)

            if line.startswith('+++'):
                got_file_start = True

        if 'aa718717d64aaba84967048c02cc894ffce62fbc' in line:
            got_commit = True

    return diff_lines


def get_original_lines(mod_script_dir, mod_script_file, line_no) -> tuple[list[str], str]:
    p = subprocess.run(["git", 'log', f'-L{line_no},+1:{mod_script_file}'],
                       capture_output=True, encoding="utf-8", shell=True, cwd=mod_script_dir, check=True)
    raw_output = p.stdout
    vanilla_lines = get_vanilla_only(raw_output.splitlines())
    return vanilla_lines, raw_output


# def get_sprite_info():
#     # TODO: add drawscene etc. here
#     is_draw_call = 'ModDrawCharacter' in line or 'DrawBustshot' in line
#     if not is_draw_call:
#         return None, None


#     match = modSpritePathCharacterNameRegex.search(line)
#     if match:
#         # 'sprite' or 'portrait'
#         sprite_type = match.group(1)
#         mod_character = match.group(2)

#         print(f"Line No: {line_index + 1} Type: {sprite_type} Char: {mod_character} Line: {line.strip()}")

#         if mod_character not in mod_to_name:
#             raise Exception(f"No mod character {mod_character} in database")

#         return sprite_type, mod_character


#     effect_match = modEffectPathRegex.search(line)
#     if effect_match:
#         effect_identifier = effect_match.group(1)

#         if effect_identifier not in mod_effect_to_name:
#             raise Exception(f"Mod Effect {effect_identifier} not found in effect database")

#         return 'effect', effect_identifier


#     return None, None

hardcoded_effect_matches = {
    # Outbreak Effects
    'effect/kamik_inf_1': 'bg/etc/inf_1', # Image of earth changing to red tint (lower number is less red)
    'effect/kamik_inf_2': 'bg/etc/inf_2',
    'effect/kamik_inf_3': 'bg/etc/inf_3',
    'effect/kamik_inf_4': 'bg/etc/inf_4',
    'effect/kamik_inf_5': 'bg/etc/inf_5',
    'effect/omo1_1': 'bg/etc/omo1_1',
    'effect/omo1_2': 'bg/etc/omo1_2',
    'effect/omo1_3': 'bg/etc/omo1_3',
    'effect/omo1_4': 'bg/etc/omo1_4',
    'effect/omo1_5': 'bg/etc/omo1_5',
    'effect/omo2_1': 'bg/etc/omo2_1',
    'effect/omo2_2': 'bg/etc/omo2_2',
    'effect/day_1': 'bg/etc/day_1',
    'effect/day_2': 'bg/etc/day_2',
    'effect/day_10': 'bg/etc/day_10',
    'effect/day_11': 'bg/etc/day_11',

    # Bus Stop Effects
    'effect/tyuui': 'img/tyuui',
    'effect/wagabu': '<USE_MODDED>',
    # For effect/wagabu, just use modded version.
    # How to handle this one? It's an image of "They went after our club", draw call doesn't exist in og game
    # I guess just copy to OGBackgrounds folder and scale for 4:3?
}


bg_match_pairs_regex_str = [
    # Outbreak
    ('_mati', '/mati'), # city (machi)
    ('cit_', '/mati'), # mod has 'cit' (city), but OG does not and uses 'mati' (machi/town) instead
    ('_sinryou', '/sinryoujo/'), # hospital
    ('_shikenkan', '/shikenkan'), # shikenkan (test tube) There is only one image of test-tubes
    ('/susuki', '/kusa'), # susuki (Miscanthus sinensis (a species of grass)) vs kusa (grass)
    ('ryoutei', 'sonozaki/ryoutei'), # ryoutei - under the sonozaki folder, it's the dining area?
    ('background/hi([^a-zA-Z]|$)', 'bg/mura/hi'), # hi (hinamizawa)
    ('background/hi([^a-zA-Z]|$)', 'bg/mura/m_hi'), # hi (hinamizawa)
    ('kamik_sono_', 'sonozakigumi/sono_'), # sonozaki building?
    ('outb_jt1', '(bg/mura2/)|(/jinja/jyt1)'), # Burning hinamizawa day
    ('outb_jyt1', 'bg/mura2/'), # Burning hinamizawa night
    ('/m_hi([^a-zA-Z]|$)', '/mura/(m_)?hi([^a-zA-Z]|$)'), # Allow matching any mod with 'm_hi' to og with 'hi' or 'm_hi'

    # Outbreak One-off entries
    ('/ta2$', '/mura/tab2$'), # Storefront (only one of these exists in mod and og)
    ('/re_s4_01$', '/ren_s3$'), # Rena's house? (only one of these exists in mod and og)
    ('/m_y4$', '/mura/y_ie2$'), # Dark hinamizawa path (only one of these exists in mod and og)
    ('/js3_01$', '/jinja/jsa7$'), # Dark inside of temple (only one of these exists in mod and og)
    ('/js3_01$', '/jinja/jsa7$'), # Dark inside of temple (only one of these exists in mod and og)
    ('/y_ie', r'koya\dy'), # Shack/hut in the forest at night

    # Bus Stop
    ('/hina_bus_', '/hina/bus_'), # Generically allow any hina bus to match with other hina busses, since the numbering is not consistent between mod and og
    ('/hina_douro_', '/hina/douro_'), # Generically allow, since the numbering is not consistent between mod and og
    ('/hina_', '/hina/'), # Generically allow any hina matching last, since naming not consistent between mod and og
    (r'/kuruma\d_', '/hina/car_'), # Car
    ('/juku([^a-zA-Z]|$)', '/mati/juku([^a-zA-Z]|$)'), # Match any juku/classroom (in mati/town)
    ('/neki1$', '/mati([^a-zA-Z]|$)'), # neki1 is shot of 3 bus stops in modern city. allow matching with any in modern city (mati)
    ('/toi_', '/wc([^a-zA-Z]|$)'), # Allow matching toilet -> toilet
    ('/sta_', '/eki([^a-zA-Z]|$)'), # Allow matching staion -> station
    ('/ng_kyo([^a-zA-Z]|$)', '/tokyo/ko([^a-zA-Z]|$)'), # I thikn this is supposed to be a classroom in Toyko
    ('/kawa([^a-zA-Z]|$)', '/damu([^a-zA-Z]|$)'), # I think mod doesn't have dedicated picture of dam, so just uses a shot of a river (kawa)    

    # Bus Stop one-off entries
    ('/hina_ryouri$', '/mati/ryouri$'), # Only appears once
    ('/hina_simen1$', '/sonota/simen1$'), # Only appears once
    ('^red$', '/hina/red1$'), # Only appears once
    ('/koudou_02$', '/mati2/mati_005$'), # Picture of city street at night
    ('/oni1$', '/hina/kawa5m$'), # Picture of river during day
    ('/kimi_ten1$', '/sion/si_h6$'), # Greyscale picture of ceiling showing lampshade
    ('/hoteru$', '/sion/si_h1$'), # Picture of hotel room showing bed
]

bg_match_pairs = [ (re.compile(p[0]), re.compile(p[1])) for p in bg_match_pairs_regex_str ] # type: list[tuple[re.Pattern, re.Pattern]]

sprite_match_pairs = []

def match_by_keyword(mod: CallData, og_call_data: list[CallData]):
    match_pairs = sprite_match_pairs if mod.is_sprite else bg_match_pairs

    # Check for outbreak variants
    # Mod has specific images for 'outb' (outbreak), but OG does not and just uses the normal versions of the image
    mod_filestem = Path(mod.path).stem
    if mod_filestem.startswith('outb_'):
        expected_og_stem = mod_filestem.replace('outb_', '')
        for og in og_call_data:
            og_filestem = Path(og.path).stem
            if expected_og_stem == og_filestem:
                return ModToOGMatch(og, None)

    # For Busstop, check for 'hina' matches
    # eg. mod is hina_bus_01 and og is hina/bus_01
    # Next section will allow more generic matching for '/hina' to '/hina/'
    if mod_filestem.startswith('hina_'):
        expected_og_key = mod_filestem.replace('hina_', '/hina/')
        for og in og_call_data:
            if expected_og_key in og.path:
                return ModToOGMatch(og, None)


    # Iterate though possible match pairs
    for mod_key, og_key in match_pairs:
        # Check the mod path contains the key, if not give up
        if mod_key.search(mod.path):
            # Now test all og lines which git returned
            for og in og_call_data:
                # Check og line contains key, if not give up
                if og_key.search(og.path):
                    return ModToOGMatch(og, None)

    return None

# Modify here if you want the analyser to only update a subset of lines
def should_reprocess(og_path):
    # if 'effect' in str(og_path):
    #     return True

    return False

def parse_graphics(
        mod_path: str,
        mod_script_dir,
        mod_script_file,
        line_index: int,
        line: str,
        statistics: Statistics,
        og_bg_lc_name_to_path: dict[str, str],
        manual_name_matching: dict[str, str],
        last_voice: str,
        voice_match_database: VoiceMatchDatabase
    ):

    print_data = ""

    # Convert the line into a CallData object
    mod = CallData(line, is_mod=True, path=mod_path)
    print_data += (f"Line No: {line_index + 1} Type: {mod.type} Key: {
                   mod.matching_key} Character: {mod.debug_character} Line: {line.strip()}\n")

    # Skip this line if the image's mod path already exists in database
    # and it has a match
    memozied_match = voice_match_database.try_get(last_voice, mod.path)
    if memozied_match is not None:
        if memozied_match.og_path is not None:
            if should_reprocess(memozied_match.og_path):
                print(f"reprocessing {mod.path} - {memozied_match.og_path}")
            else:
                return

    # Now use git to extract matching lines from the original game
    og_lines, raw_git_log_output = get_original_lines(
        mod_script_dir, mod_script_file, line_index + 1)

    # If this is a sprite, but the character is not recognized, just give up as we need to update the character database
    if mod.matching_key is not None and common.missing_character_key in mod.matching_key:
        statistics.add_missing_character(mod.matching_key, line, og_lines)
        voice_match_database.set(VoiceBasedMatch(last_voice, mod, None))
        return

    print_data += ">> Raw Git Log Output (vanilla -> mod) <<\n"
    for l in og_lines:
        print_data += l + "\n"
    # print_data += raw_git_log_output
    print_data += ">> END Git Log Output <<\n"

    # Now try to match lines using various methods
    mod_to_og_match = None

    # Extract all graphics found in the og lines
    og_call_data = [] #type: list[CallData]
    if mod_to_og_match is None:
        for l in og_lines:
            for og_path in graphics_identifier.get_graphics_path_on_line(l, is_mod=False):
                og_call_data.append(CallData(l, is_mod=False, path=og_path))

        mod.debug_og_call_data = og_call_data

        if len(og_call_data) == 0:
            msg = f'>> No OG graphics for {line}\n'

            if len(og_lines) > 0:
                msg += 'OG lines were:\n'
                for l in og_lines:
                    msg += f"{l}\n"

            # print(msg)
            print_data += msg

    # og_call_data = [CallData(l, is_mod=False) for l in og_lines]

    for og in og_call_data:
        print_data += (
            f"- Type: {og.type} Matching Key: {og.matching_key} Line: {og.line.strip()}\n")

        if not og.line.startswith('+'):
            raise Exception(f"git output for {
                            og.line} does not start with a +")

    print_data += ("\n")

    # if len(og_lines) == 1:
    #     matched_line = og_lines[0]
    #     print(f"Matched as only one match: {matched_line}")

    # scene folder are special CGs, so dont' try to match them
    if mod_to_og_match is None:
        if mod.path.startswith('scene/'):
            mod_to_og_match = ModToOGMatch(None, '<SPECIAL_SCENE>')
        elif textRegex.search(mod.path):
            if mod.path in hardcoded_effect_matches:
                print(f"Did hardcoded effect match from {mod.path} -> {hardcoded_effect_matches[mod.path]}")
                mod_to_og_match = ModToOGMatch(None, hardcoded_effect_matches[mod.path])
            else:
                mod_to_og_match = ModToOGMatch(None, '<SPECIAL_TEXT_EFFECT>')

    if mod_to_og_match is None:
        if mod.matching_key:
            # First try to do exact match if the path contains a matching folder
            if mod_to_og_match is None:
                for og in og_call_data:
                    if og.is_sprite and f'/{mod.matching_key}/' in og.path:
                        mod_to_og_match = ModToOGMatch(og, None)
                        print_data += (f"Matched by matching key in path (exact folder): {mod_to_og_match}\n")
                        break

            # This part never seems to be executed, and may generate bad matches, so I've commented it out for now
            # # Then just match anywhere in the path
            # if mod_to_og_match is None:
            #     for og in og_call_data:
            #         if og.is_sprite and mod.matching_key in og.path:
            #             mod_to_og_match = ModToOGMatch(og, None)
            #             print_data += (f"Matched by matching key in path (anywhere in path): {mod_to_og_match}\n")
            #             break

    # Extra matching specifically for the OG sprites in the 'sonota' folder
    # CG\sprites\sonota\oisi[VARIANTS] and
    # CG\sprites\sonota\tetu[VARIANTS]
    sonota_keys = [
        # Sprites
        ('sprite/oisi', 'sprites/sonota/oisi', True),
        ('sprite/tetu', 'sprites/sonota/tetu', True),
        # Backgrounds
        ('background/ke_shiryou1_02', 'img/dsc000', False), # In OG game, img folder contains various pictures of Ryukishi's room, but they are all mapped to ke_shiryou1_02 in our mod
    ]
    if mod_to_og_match is None:
        for mod_key, og_key, target_sprites in sonota_keys:
            if mod.is_sprite == target_sprites:
                if mod_key in mod.path:
                    for og in og_call_data:
                        if og.is_sprite == target_sprites and og_key in og.path:
                            mod_to_og_match = ModToOGMatch(og, None)
                            print_data += (f"Matched by 'sonota' special case: {mod_to_og_match}\n")
                            break

    # Try matching by same name match
    if mod_to_og_match is None:
        for og in og_call_data:
            if og.name == mod.name:
                print(f"Matched by name in git log '{
                      og.name}': {mod.path} -> {og.path}")
                mod_to_og_match = ModToOGMatch(og, None)
                break

    # Try matching by manual matches
    # Eg 'oki_pool2' : 'pool2' which comes from the files:
    #  "background/oki_pool2" -> "bg/2021_add/pool2"
    if mod_to_og_match is None:
        if mod.name in manual_name_matching:
            expected_og_name = manual_name_matching[mod.name]
            for og in og_call_data:
                if og.name == expected_og_name:
                    mod_to_og_match = ModToOGMatch(og, None)
                    msg = f"Matched by manual name match '{mod.name}' -> '{expected_og_name}' {mod_to_og_match}\n"
                    print(msg)
                    print_data += msg
                    break

    # Try matching by same name in OG files
    if mod_to_og_match is None:
        if mod.name in og_bg_lc_name_to_path:
            og_path = og_bg_lc_name_to_path[mod.name]
            print(f"Matched by name in og files '{
                  mod.name}': {mod.path} -> {og_path}")
            mod_to_og_match = ModToOGMatch(None, og_path)

    # Try matching by match keywords
    if mod_to_og_match is None:
        keyword_match = match_by_keyword(mod, og_call_data)
        if keyword_match:
            mod_to_og_match = keyword_match

    # Try to match by guessing for BGs, if there is only one possible option it could be
    if mod_to_og_match is None:
        if not mod.is_sprite:
            last_match = None
            match_count = 0
            for og in og_call_data:
                if not og.is_sprite:
                    if mod.path.startswith('background/') and og.path.startswith('bg/'):
                        last_match = og
                        match_count += 1

            if match_count == 1:
                print(f"Matched Background by guess as only one possibility '{mod.name}': {mod.path} -> {last_match.path}")
                mod_to_og_match = ModToOGMatch(last_match, None)

    if mod_to_og_match is None:
        print_data += ("Failed to match line\n")
        print(f"Failed to match '{mod.name}' line: {line.strip()} lastVoice: {last_voice}")
        for og in og_call_data:
            print(f"- Type: {og.type} Matching Key: {og.matching_key} Line: {og.line.strip()}")

        statistics.match_fail += 1

        # Record what possible matches there could be for analysis
        statistics.record_guesses(mod, og_call_data)
    else:
        statistics.match_ok += 1
        statistics.add_match(mod, mod_to_og_match)

    voice_match_database.set(VoiceBasedMatch(last_voice, mod, mod_to_og_match))

    print_data += ('----------------------------------------\n')

    # if 'ModDrawCharacter' in line or 'DrawBustshot' in line:
    #     match = modSpritePathCharacterNameRegex.search(line)
    #     if match:
    #         # 'sprite' or 'portrait'
    #         sprite_type = match.group(1)
    #         mod_character = match.group(2)
    #     else:
    #         raise Exception(f"No character found in line {line}")

    #     print(f"Line No: {line_index + 1} Type: {sprite_type} Char: {mod_character} Line: {line.strip()}")

    #     if mod_character not in mod_to_name:
    #         raise Exception(f"No mod character {mod_character} in database")

    #     original_lines = get_original_line(mod_script_dir, mod_script_file, line_index + 1)

    #     for line in original_lines:
    #         # determine what character is being displayed
    #         match = ogSpritePathCharacterNameRegex.search(line)
    #         if match:
    #             character = match.group(1)
    #             print(f'{character}: {line.strip()}')

    #             # if character not in og_to_name:
    #             #     raise Exception(f"No og character {character} in database")
    #         else:
    #             print(line)
    # else:
    #     effect_match = modEffectPathRegex.search(line)

    return print_data

def parse_line(mod_script_dir, mod_script_file, all_lines: List[str], line_index, line: str, statistics: Statistics, og_bg_lc_name_to_path: dict[str, str], manual_name_matching: dict[str, str], last_voice: str, voice_match_database: VoiceMatchDatabase):
    """This function expects a modded script line as input, as well other arguments describing where the line is from"""

    # for now just ignore commented lines
    line = line.split('//', maxsplit=1)[0]

    all_print_data = ""

    for mod_graphics_path in graphics_identifier.get_graphics_path_on_line(line, is_mod=True):
        print_data = parse_graphics(mod_graphics_path, mod_script_dir, mod_script_file, line_index, line, statistics, og_bg_lc_name_to_path, manual_name_matching, last_voice, voice_match_database)
        if print_data:
            all_print_data += print_data

    return all_print_data


def scan_one_script(mod_script_dir: str, mod_script_path: str, debug_output_file, global_result: GlobalResult, output_folder: str):
    os.makedirs(output_folder, exist_ok=True)
    voice_db_path = common.get_voice_db_path(mod_script_path)

    if Path(voice_db_path).exists():
        print(f"Using existing database at [{voice_db_path}]")
        voice_match_database = VoiceMatchDatabase.deserialize(voice_db_path)
    else:
        print(f"Creating new existing database at [{voice_db_path}]")
        voice_match_database = VoiceMatchDatabase(mod_script_path)

    stats = Statistics()

    with open(mod_script_path, encoding='utf-8') as f:
        all_lines = f.readlines()

    # Check every line in the modded input script for corresponding og graphics
    last_voice = None
    for line_index, line in enumerate(all_lines):
        if max_lines != None and line_index > max_lines:
            break

        voice_on_line = voice_util.get_voice_on_line(line)
        if voice_on_line:
            voice_match_database.acknowledge_voice(voice_on_line)
            last_voice = voice_on_line

        print_data = parse_line(mod_script_dir, mod_script_path,
                                all_lines, line_index, line, stats, og_bg_lc_name_to_path, manual_name_matching, last_voice, voice_match_database)

        # Print output for debbuging, only if enabled
        if debug_output_file is not None:
            debug_output_file.write(line)
            if print_data is not None:
                debug_output_file.write(print_data)

    print(f"Saving voice match databse to [{voice_db_path}]")
    voice_match_database.serialize(voice_db_path)

    # Write the output statistcs .json
    # print(f"{stats.match_ok}/{stats.total()} Failed: {stats.match_fail}")
    # print(stats.count_statistics)
    out_filename = Path(mod_script_path).stem
    json_out_path = os.path.join(output_folder, f'{out_filename}.json')
    missing_chars_path = os.path.join(output_folder, f'{out_filename}_missing_chars.txt')

    stats.save_as_json(json_out_path, missing_chars_path, global_result)


# with open('debug_output.txt', 'w', encoding='utf-8') as debug_output:

manual_name_matching = {
    'oki_pool1' : 'pool1',
    'oki_pool2' : 'pool2',
    'oki_pool3' : 'pool3',
    # 'chika_shisetsu': 'ko1',
    # Effect images...should these be handled differently?
    'hara': 'ara_d4',
    'hton': 'ton_d5a',
}


# Modify to only test a subset of the files/each file
max_lines = None
pattern = '*.txt'

# Build a mapping from filename -> path for unmodded CGs, except sprites
unmodded_cg = 'D:/games/steam/steamapps/common/Higurashi When They Cry Hou+ Unmodded/HigurashiEp10_Data/StreamingAssets/CG'

if not os.path.exists(unmodded_cg):
    raise Exception(f"Unmodded CG path doesn't exist: {unmodded_cg}")

og_bg_lc_name_to_path = path_util.lc_name_to_path(
    unmodded_cg, exclude=['sprites/'])

mod_script_dir = 'D:/drojf/large_projects/umineko/HIGURASHI_REPOS/10 hou-plus/Update/'

output_folder = 'stats_temp'

debug_folder = 'script_with_debug'
os.makedirs(debug_folder, exist_ok=True)

# TODO: add global stats across all items? only write out once all items processed
global_result = GlobalResult()

for modded_script_path in Path(mod_script_dir).glob(pattern):
    debug_output_path = os.path.join(debug_folder, modded_script_path.name)
    with open(debug_output_path, 'w', encoding='utf-8') as debug_output_file:
        scan_one_script(mod_script_dir, modded_script_path, debug_output_file, global_result=global_result, output_folder=output_folder)

if global_result.missing_char_detected:
    print("<<<<<<<<<<< WARNING: one or more missing from the mod_to_name or og_to_name table, please update or matching will be incomplete! >>>>>>>>>>>>>>")

#################################################################
#### Now run 'verification_and_fallback_matching.py' script after this. Refer to Readme.md
#################################################################
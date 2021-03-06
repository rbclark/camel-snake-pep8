#!/usr/bin/env python2
"""

camel-snake-pep8
================

A refactoring tool to help convert camel case to snake case and vice versa in a
Python program, in conformity with the PEP-8 style guide.  It uses/abuses
Python-Rope to do find and perform the changes.

**Use this software at your own risk.**

Currently only runs on Python 2, but can refactor Python 2 and Python 3.

..  Copyright (c) 2017 by Allen Barker.
    License: MIT, see LICENSE for more details.

"""

# NOTE: Someone who knows rope better might have a better way to get all the
# names and offsets from the files, and perhaps take scoping into account on
# the warnings.

# Possible enhancements:
# - Better command line with more options.
# - Logging.

from __future__ import print_function, division
import sys
import os
import re
import itertools
from collections import defaultdict

import rope
from rope.base.project import Project
from rope.base.libutils import get_string_scope, get_string_module # Not currently used.
from rope.refactor.rename import Rename
from rope.base import worder
from colorama import Fore, Back, Style

change_function_and_method_names = True
change_function_and_method_arguments = True
change_function_and_method_keywords = True
change_assigned_variables = True
change_class_names = True

BANNER_WIDTH = 78

BLUE_INFO_COLOR = Fore.BLUE + Style.BRIGHT
YELLOW_WARNING_COLOR = Fore.YELLOW
RED_ERROR_COLOR = Fore.RED
NEW_NAME_COLOR = Fore.GREEN
CURR_NAME_COLOR = Fore.CYAN
RESET = Style.RESET_ALL

REJECTED_CHANGE_MAGIC_COOKIE = "_XxX_CamelSnakePep8_PreserveName_XxX_"

SOA_FOLLOWED_CALLS = 1 # Depth of calls in Rope static analysis (Rope default is 1).

#
# Dicts and sets for saving names from files and related functions.
#

original_names_sets_dict = {} # Original names in files, keyed by realpath to the files.
final_names_sets_dict = {} # The final names in files, after all changes.

modified_modules_set = set() # Set containing the realpaths of modified modules.

def save_set_of_all_names_in_module(file_realpath, save_dict):
    """Get the names in the file and save in the dict `save_dict` keyed by
    the realpath."""
    names_in_module = rope_iterate_worder(file_realpath, unfiltered=True)
    name_set = set(c[0] for c in names_in_module)
    if file_realpath not in save_dict:
        save_dict[file_realpath] = name_set

user_accepted_changes_sets_dict = defaultdict(set) # Changes accepted by the user.
user_rejected_changes_sets_dict = defaultdict(set) # Changes rejected by the user.
rope_rejected_changes_sets_dict = defaultdict(set) # Changes rejected by rope.

def save_changes(realpath_list, change, user=True, accepted=True):
    """Save one change or rejected change, keyed by the corresponding module pathnames
    in `realpath_list`.  Offset information is removed from the middle of any 3-tuple
    changes since it does not remain valid."""
    if len(change) > 2:
        change = (change[1], change[3])
    for path in realpath_list:
        if user and accepted:
            user_accepted_changes_sets_dict[path].add(change)
        elif user and not accepted:
            user_rejected_changes_sets_dict[path].add(change)
        else:
            rope_rejected_changes_sets_dict[path].add(change)

def compare_changes_with_final_names(module_realpath_list, changes_dict, accepted=True):
    """This routine does the real work for `analyze_names_in_final_state`, looping
    over the designated changes and modules."""
    if accepted:
        accept_or_reject_word = "Accepted"
        accept_reject_warning = ("   The original name before the change still appears"
                                " in these modules:")
    else:
        accept_or_reject_word = "Rejected"
        accept_reject_warning = ("   The name suggested but rejected appears in these"
                                " modules:")

    for path, change_set in changes_dict.items():
        printed_header = False
        for name, new_name in change_set:
            found_file_paths = set()
            for module_realpath in module_realpath_list:
                final_names_set = final_names_sets_dict[module_realpath]
                name_to_search_for = name if accepted else new_name
                if name_to_search_for in final_names_set:
                    found_file_paths.add(module_realpath)
            if found_file_paths:
                if not printed_header:
                    print_warning("Warnings for module {0}".format(
                        os.path.relpath(path, project_dir_realpath)), "\n")
                    printed_header = True
                print("   {0} change: {1} to {2}.".format(
                                      accept_or_reject_word,
                                      CURR_NAME_COLOR + name + RESET,
                                      NEW_NAME_COLOR + new_name + RESET))
                print_warning(accept_reject_warning)
                for fpath in sorted(found_file_paths):
                    print("      ", os.path.relpath(fpath, project_dir_realpath))
                print()

def analyze_names_in_final_state(module_realpath_list):
    """Analyze the final names in the each module originally passed into the program,
    giving warnings about those which could potentially have problems.  This is run
    after all changes have been made.

    For each rejected change, look for any module which has the suggested name
    that was rejected.  These might have been changed in one place and not
    another, but not on purpose.  Vice versa for accepted changes."""

    print_banner("Doing post-processing name analysis on all modules.", big=True)

    # Get the final names from all the modules.
    for module_realpath in module_realpath_list:
        save_set_of_all_names_in_module(module_realpath, save_dict=final_names_sets_dict)

    # Issue warnings.
    print_warning("Any warnings below are only for potential problems.  Most will probably"
                  "\nnot be problems.  No scoping information is taken into account in"
                  " this\nanalysis.\n")

    print_banner("User-rejected change information.", char="-")
    compare_changes_with_final_names(module_realpath_list,
                                     user_rejected_changes_sets_dict,
                                     accepted=False)

    print_banner("Rope-rejected change information.", char="-")
    compare_changes_with_final_names(module_realpath_list,
                                     rope_rejected_changes_sets_dict,
                                     accepted=False)

    print_banner("Accepted change information.", char="-")
    compare_changes_with_final_names(module_realpath_list,
                                     user_accepted_changes_sets_dict,
                                     accepted=True)

#
# Temporary renaming for rejected changes.
#

change_reject_counter = 0 # Make the temporary names unique.

def create_rejected_change_preserve_name(name):
    """Create the new name mangled with the magic cookie (which will be reverted later)."""
    global change_reject_counter
    change_reject_counter += 1
    return name + REJECTED_CHANGE_MAGIC_COOKIE + str(change_reject_counter)

def remove_rejected_change_magic_cookies(modified_modules_set):
    """Remove the magic cookie strings for rejected changes, for all modules
    in `modified_modules_set`."""
    for fname in modified_modules_set:
        with open(fname) as f:
            file_string = f.read()

        with open(fname, "w") as f:
            f.write(restore_text(file_string))

def restore_text(text):
    """Remove the magic cookie from a mangled name."""
    # Compiled regex should be cached, so no need to explicitly compile.
    remove_cookie = REJECTED_CHANGE_MAGIC_COOKIE + r"\d+"
    text = re.sub(remove_cookie, "", text)
    return text

#
# Simple utility functions.
#

first_cap_re = re.compile('(.)([A-Z][a-z]+)') # Used in camel_to_snake.
all_cap_re = re.compile('([a-z0-9])([A-Z])')  # Used in camel_to_snake.

def camel_to_snake(name):
    """Convert possible camel case string to snake case.  Anything with all caps
    and underscores is left unmodified (might be a constant)."""
    # Modified from: http://stackoverflow.com/questions/1175208/
    if all(c.isupper() or c == "_" for c in name):
        return name
    s1 = first_cap_re.sub(r'\1_\2', name)
    return all_cap_re.sub(r'\1_\2', s1).lower()

def snake_to_camel(name):
    """Convert snake case names to camel case with first word capitalzed.
    Always capitalizes first letter (to handle camel case starting with
    lower case).  Preserves one leading underscore if present."""
    leading_underscore = False
    if name and name[0] == "_":
        leading_underscore = True
    name = name[0].upper() + name[1:]
    words = name.split("_")
    if len(words) == 1 or (len(words) == 2 and leading_underscore):
        return name
    cap_words = [w.capitalize() for w in words]
    joined_name = "".join(cap_words)
    if leading_underscore:
        joined_name = "_" + joined_name
    return joined_name

def get_source_string(fname):
    """Get string version of the source code in the file with the filename
    passed in."""
    with open(fname, "r") as source_file:
        source_string = source_file.read()
    return source_string

def colorize_string(color, string):
    """Convert a string to a Colorama colorized string."""
    return color + string + RESET

def print_color(color, *args, **kwargs):
    """Like print, but with a color argument."""
    kwargs2 = kwargs.copy()
    kwargs2["end"] = ""
    print(color, sep="", end="")
    print(*args, **kwargs2)
    print(RESET, **kwargs)

def print_info(*args, **kwargs):
    print_color(BLUE_INFO_COLOR, *args, **kwargs)

def print_warning(*args, **kwargs):
    print_color(YELLOW_WARNING_COLOR, *args, **kwargs)

def print_error(*args, **kwargs):
    print_color(RED_ERROR_COLOR, *args, **kwargs)

def print_banner(text, big=False, char="="):
    """Print out the text in a banner."""
    c = BLUE_INFO_COLOR
    print_color(c, char * BANNER_WIDTH)
    if big:
        print_color(c, char * BANNER_WIDTH)
    print_color(c, char * 5, " ", text, " ",
                char * (BANNER_WIDTH - 7 - len(text)), sep="")
    print_color(c, char * BANNER_WIDTH)
    if big:
        print_color(c, char * BANNER_WIDTH)
    print()

def filename_to_module_name(fname):
    """Return the module name from a filename.  Not fully qualified with the
    package root name, though."""
    # The commented-out code below gives correct dotted module paths, but Rope
    # doesn't return the full changes description for that like it does when
    # when passed the shorter module path name (leaving off the root).
    #
    #if project_is_package:
    #    relative_dir = os.path.realpath(os.path.join(project_dir, ".."))
    #else:
    #    relative_dir = os.path.realpath(project_dir)
    relative_dir = project_dir_realpath

    abs_fname = os.path.realpath(fname)
    relpath = os.path.relpath(abs_fname, relative_dir)
    relpath = relpath[:-3]
    module_name = relpath.replace(os.path.sep, ".")
    return module_name

def unique_everseen(iterable, key=None):
    "List unique elements, preserving order. Remember all elements ever seen."
    # unique_everseen('AAAABBBCCDAABBB') --> A B C D
    # unique_everseen('ABBCcAD', str.lower) --> A B C D
    # https://docs.python.org/3/library/itertools.html#recipes
    seen = set()
    seen_add = seen.add
    if key is None:
        for element in itertools.ifilterfalse(seen.__contains__, iterable):
            seen_add(element)
            yield element
    else:
        for element in iterable:
            k = key(element)
            if k not in seen:
                seen_add(k)
                yield element

#
# Parsing function parameter strings (to find the parameters without default values).
#

def process_param(param, offset):
    """Process a single parameter produced by `get_function_parameter_names`."""
    # Ignore args with default values, since Rope considers them assignments.
    if "=" in param:
        return []

    # Strip off any type annotation.
    first_colon_index = param.find(":")
    if first_colon_index >= 0: # Variables are first in MyPy, reversed from C.
        param = param[:first_colon_index]

    # Strip off beginning whitespace.
    first_non_whitespace_index = len(param) - len(param.lstrip())
    offset += first_non_whitespace_index
    param = param.strip()
    if not param:
        return []
    return [param, offset]

def get_function_param_names(initial_fun_string, initial_offset, fun_name_string):
    """Parse a function string returned by Rope's
    `get_function_and_args_in_header` to get the parameter names which are not
    assigned default values (since those are taken care of in the
    variable-assignment group).  The function name `fun_name_string` is passed
    only as an error check to make sure the name found in the initial offset
    search matches the name in the string returned by the Rope function."""
    fun_string = initial_fun_string
    offset = initial_offset

    if not fun_string:
        return []

    # Do some initial preprocessing.
    index = fun_string.find("(") + 1
    fun_name = fun_string[:index-1]
    assert fun_name == fun_name_string # Error check.
    fun_string = fun_string[index:].split("->")[0] # Remove name and return type.
    fun_string = fun_string.rstrip()
    offset += index
    index = 0 # Keep a local index relative to first char of first arg.

    # Make into a list of characters.
    close_paren_index = fun_string.rfind(")") # Note we need rfind here.
    fun_string = fun_string[:close_paren_index+1]
    fun_list = [c for c in fun_string]
    assert fun_list[close_paren_index] == ")"
    fun_list[close_paren_index] = "," # Map close paren to comma for consistency later.

    # Turn all unneeded chars into spaces, including all inside any paren nesting.
    simplified_fun_list = []
    paren_count = 0
    for c in fun_list:
        if c in "([{":
            paren_count += 1
            simplified_fun_list.append(" ")
        elif c in ")]}":
            paren_count -= 1
            simplified_fun_list.append(" ")
        elif paren_count > 0 or c == "*":
            simplified_fun_list.append(" ")
        else:
            simplified_fun_list.append(c)
    fun_string = "".join(simplified_fun_list)

    # Separate the arguments and call process_params on them.
    final_name_list = []
    while True:
        comma_index = fun_string.find(",", index)
        if comma_index < 0:
            break
        arg_string = fun_string[index:comma_index]
        if arg_string:
            name_and_offset = process_param(arg_string, offset + index)
            if name_and_offset:
                final_name_list.append(name_and_offset)
        index = comma_index + 1

    for n in final_name_list:
        for i in range(len(n[0])):
            assert n[0][i] == initial_fun_string[n[1]-initial_offset + i]
    return final_name_list

#
# Functions that do the real work.
#

def experiment_with_scoping_classes(project, source_file_name):
    """This is not used; just for experimenting with `PyObject` and `Scope` objects."""
    def dir_no_magic(obj):
        return [s for s in dir(obj) if s[:2] != "__"]

    source_string = get_source_string(source_file_name)

    # Get a `Worder` for the code.
    w = worder.Worder(source_string)
    print("\nGot a Worder.  The dir is", dir_no_magic(w))

    # Get a PyObject for the code.
    py_object = get_string_module(project, source_string, resource=None,
                                  force_errors=False)
    print("\nGot a PyObject.  The dir is", dir_no_magic(py_object))

    # Get a Scope object for the code.
    scope_object = get_string_scope(project, source_string, resource=None)
    print("\nGot a Scope object.  The dir is", dir_no_magic(scope_object))
    print("The names in the Scope are:\n", scope_object.get_names())


def rope_iterate_worder(source_file_name, fun_name_defs=False, fun_arguments=False,
                        fun_keywords=False, assigned_vars=False, class_names=False,
                        unfiltered=False):
    """Get all the names of a given type and their offsets.  The `project` argument
    is not currently used.

    Due to how rope works these are split up in an unusual way.  The function arguments
    without default values are parsed out of the string representing the function
    and its arguments.

    fun_name_defs = all function and method defs
    fun_arguments = function arguments which do now have default values
    fun_keywords = function keywords (which duplicate the assigned vars changes)
    assigned_vars = any variables which are assigned, including keyword parameters

    """
    # Currently based on Worder class:
    # https://github.com/python-rope/rope/blob/master/rope/base/worder.py
    if unfiltered:
        fun_name_defs = True
        fun_arguments = True
        fun_keywords = True
        assigned_vars = True
        class_names = True

    source_string = get_source_string(source_file_name)
    w = worder.Worder(source_string)

    possible_changes = []
    unidentified_words = []
    upcoming = None
    offset = 0
    while True:
        try:
            word = w.get_word_at(offset)
        except (ValueError, IndexError):
            break

        if w.is_function_keyword_parameter(offset) and fun_keywords:
            possible_changes.append([word, offset, camel_to_snake(word)])

        elif (w.is_assigned_here(offset) or w.is_assigned_in_a_tuple_assignment(offset)
                ) and assigned_vars: # Tuple is check probably redundant; doesn't work.
            possible_changes.append([word, offset, camel_to_snake(word)])

        elif word == "for":
            upcoming = "for"

        elif word == "def":
            upcoming = "def"

        elif word == "class":
            upcoming = "class"

        elif upcoming == "for" and assigned_vars:
            possible_changes.append([word, offset, camel_to_snake(word)])
            upcoming = None

        elif upcoming == "def" and w.is_a_class_or_function_name_in_header(offset):
            if fun_name_defs:
                possible_changes.append([word, offset, camel_to_snake(word)])
            upcoming = None

            try:
                # TODO? NOTE: Adding -4 to offset below was needed to make the
                # CURRENT function name being detected in this branch of the
                # `if` match the function and args that are returned by the
                # call below!  Otherwise, you always got a function name, but
                # the function below returned the string for the one that is
                # ahead in text...
                #
                # The value -4 is the minimum (in abs) to make them match, and
                # it makes the offsets of the parameters without default
                # arguments exactly match the ones found below in the
                # "unidentified" section... This works, but I do not know why.
                fun_and_args = w.get_function_and_args_in_header(offset-4)
            except (ValueError, IndexError):
                fun_and_args = None
            #print("Fun and args string from rope is:", fun_and_args)
            if fun_arguments:
                param_and_offset_list = get_function_param_names(
                                                           fun_and_args, offset, word)
                param_list_and_new = [n + [camel_to_snake(n[0])]
                                               for n in param_and_offset_list]
                #print("param_list_and_new", param_list_and_new)
                possible_changes += param_list_and_new

        elif upcoming == "class" and w.is_a_class_or_function_name_in_header(offset):
            if class_names:
                possible_changes.append([word, offset, snake_to_camel(word)])
            upcoming = None

        else:
            unidentified_words.append([word, offset])

        # Move the offset pointer ahead until the recognized word changes.
        break_outer = False
        while True:
            offset += 1
            try:
                next_word = w.get_word_at(offset)
                if next_word != word:
                    break
            except (ValueError, IndexError):
                break_outer = True
                break
        if break_outer:
            break

    if unfiltered:
        return possible_changes + unidentified_words

    # Filter out the possible changes that are already in snake case, save new name.
    filtered_changes = []
    for c in possible_changes:
        name = c[0]
        new_name = c[2]
        if REJECTED_CHANGE_MAGIC_COOKIE in name:
            continue
        if new_name == name:
            continue
        filtered_changes.append(tuple(c))

    # Remove duplicates and return.
    unique_changes_generator = unique_everseen(filtered_changes)
    filtered_changes = [c for c in unique_changes_generator]
    return filtered_changes

def get_renaming_changes(project, module, offset, new_name, name, source_file_name,
                         docs=True):
    """Get the changes for doing a rename refactoring.  If Rope raises a
    `RefactoringError` it prints a warning and returns `None`."""
    try:
        changes = Rename(project, module, offset).get_changes(
                                       new_name, docs=docs, unsure=None)
        return changes
    except rope.base.exceptions.RefactoringError:
        print("Error in calculating a rename from '{0}' to '{1}' in file"
              "\n   {2}".format(name, new_name, source_file_name))
    return None

def rope_rename_refactor(project, source_file_name, possible_changes, docs=True):
    """Query the user about changes to make.  Do at most one change (since all
    the possible change offsets are generated again after a change).  If a
    change is done return true, otherwise if there are no changes to make
    return false.  Rejected changes are actually made (to include a magic
    cookie) and then are deleted later."""
    # Example refactor at:
    #    https://github.com/python-rope/rope/blob/master/docs/library.rst
    # NOTE: Rename(project, resource, offset), where project and offset are
    # described below.  Offset is a character count into a resource, which in
    # this case is a module.  Offset of None refers to the resource itself.
    if not possible_changes:
        return False

    module_name = filename_to_module_name(source_file_name)
    module = project.find_module(module_name)

    for name, offset, new_name in possible_changes:
        while True:
            skip_change = False # Skip changes that rope simply cannot resolve.
            changes = get_renaming_changes(project, module, offset, new_name, name,
                                           source_file_name, docs=docs)
            if not changes:
                skip_change = True
                break
            change_string = changes.get_description()
            changed_resources = list(changes.get_changed_resources())

            # Calculate changed resources and possible warnings.
            warning = False
            existing_name_modules = []
            conversion_collisions = []
            modules_to_change_realpaths = []
            modules_to_change_names = []
            for c in changed_resources:
                #print("   Path of resource changed:", c.path)
                #print("   Name of resource changed:", c.name)
                #print("   Name of resource changed:", c.real_path)

                # Warnings for new name originally in module that would be changed.
                modules_to_change_realpaths.append(c.real_path)
                modules_to_change_names.append(c.name)
                save_set_of_all_names_in_module(
                        c.real_path, save_dict=original_names_sets_dict)
                if new_name in original_names_sets_dict[c.real_path]:
                    warning = True
                    existing_name_modules.append(c.name)
                modified_modules_set.add(c.real_path)

                # Warning for name collision with previous change.
                for accepted_name, accepted_new_name in \
                                          user_accepted_changes_sets_dict[c.real_path]:
                    if new_name == accepted_new_name and accepted_name != name:
                        warning = True
                        conversion_collisions.append(
                                          [c.name, accepted_name, accepted_new_name])

            # Colorize the description and print it out for the user to view.
            color_new_name = colorize_string(NEW_NAME_COLOR, new_name)
            color_name = colorize_string(CURR_NAME_COLOR, name)
            change_string = change_string.replace(name, color_name)
            change_string = change_string.replace(new_name, color_new_name)
            # TODO, maybe: Could also remove any REJECTED_CHANGE_MAGIC_COOKIE strings.
            print_info("Changes are:")
            print("   ", change_string)
            print_info("Modules which would be changed:")
            for m in modules_to_change_names:
                print("   ", m)
            print()

            # Print any warnings.
            if existing_name_modules:
                print_warning(
                        "Warning: The new name '{0}' already existed somewhere in"
                        " the modules\nto change before this run of the program made"
                        " any changes.  This may or may not\ncause a name collision."
                        " Scoping was not taken into account in the analysis.\n"
                        "\nThe modules it was found in are:"
                        .format(new_name))
                for m in existing_name_modules:
                    print("   ", m)
                print()
            if conversion_collisions:
                print_warning(
                        "Warning: Already accepted a rename of a different name to the"
                        " new name\n'{0}' in one of the modules to change.  This"
                        " may or may not\ncause a name collision.  Scoping was not taken"
                        " into account in the analysis.\n".format(new_name))
                print_warning("The modules and previously-accepted changes are:")
                for m in conversion_collisions:
                    print("   In '{0}' changed '{1}' to '{2}'.".format(m[0], m[1], m[2]))
                print()

            # Query the user.
            print_info("Do the changes? [yncd] ", end="")
            yes_no = raw_input("").strip()
            if not yes_no or yes_no not in "dcyYnN": # Set default reply.
                if warning: yes_no = "n"
                else: yes_no = "y"
            if yes_no == "c":
                print_info("\n", "-" * BANNER_WIDTH, "\n", sep="")
                print_info("Enter a different string: ", end="")
                new_name = raw_input("")
                print()
                continue
            if yes_no == "d":
                print_info("\n", "-" * BANNER_WIDTH, "\n", sep="")
                print_info(
                      "Temporarily toggling the docs setting to {0} for this change.\n"
                      .format(not docs))
                docs = not docs
                continue
            elif yes_no in "yY":
                save_changes(modules_to_change_realpaths, (name, new_name),
                                                          user=True, accepted=True)
                project.do(changes)
            else:
                # Do not do the change; rename to a temp name to preserve old name.
                skip_change = False
                save_changes(modules_to_change_realpaths, (name, new_name),
                                                          user=True, accepted=False)
                changes = get_renaming_changes(project, module, offset,
                              create_rejected_change_preserve_name(name),
                              name, source_file_name, docs=False)
                if not changes:
                    skip_change = True
                    break
                project.do(changes)

            break
        if skip_change: # Changes skipped because Rope raised an exception.
            print("Rope could not properly resolve the change, or some other Rope problem.")
            print("Rejecting the change...\n")
            print_info("-" * BANNER_WIDTH)
            print()
            save_changes([source_file_name], (name, new_name), user=False, accepted=False)
            continue
        print()
        print_info("-" * BANNER_WIDTH)
        print()
        return True

    return False

#
# Process command-line arguments.
#

if not len(sys.argv) >= 3:
    print("Usage: camel_snake_pep8 <packageOrProjectDir> "
                                      "<fileToModify> [<fileToModify> ...]",
          file=sys.stderr)
    sys.exit(1)

project_dir = sys.argv[1]
project_dir_realpath = os.path.realpath(project_dir)
if not os.path.isdir(project_dir_realpath):
    print_error("Error: First argument is not a directory.")
    sys.exit(1)
project_is_package = False
if os.path.exists(os.path.join(project_dir, "__init__.py")):
    project_is_package = True

fname_list = sys.argv[2:]
for f in fname_list:
    if not f[-3:] == ".py":
        print_warning("Warning: All arguments after the first must end in '.py' (or"
                      "\nRope will have problems).  This file did not:\n   ", f)
        sys.exit(1)

def main():
    """Run the program."""

    print_banner("Running camel_snake_pep8.")

    if project_is_package:
        print("The project is detected as a Python package.")
    else:
        print("The project is detected to be non-package Python scripts.")

    print_warning("\nBe sure to make a backup copy of all files before running this"
                  "\nprogram. All changes are made to the files in-place.\n")

    print("The default reply for queries (e.g. with enter) when no warning/caution"
          "\nis given is 'y', i.e, do the changes.  If a warning/caution is given then"
          "\nthe default reply is 'n'.")

    print("\nEntering 'c' will query for a changed name string from the user."
          "\nIf the new name is still not the proper form you will then be queried"
          "\nagain about changing it (which you can say no to if it is what you want).")

    print("\nIt is safer to make all changes to a given package/module"
          " in the same run of\nthe program because warnings of"
          " possible collisions will be more accurate.")

    print("\nModifying the docs changes the names in strings, too.  This is convenient,"
          "\nbut things like dict keys will also be changed.  If you choose to modify"
          "\ndocs you can still select 'd' on viewing individual changes to toggle the"
          "\nsetting off temporarily.")

    print_info("Modify docs (default is 'n')? ", end="")
    docs = raw_input("")
    if docs and docs in "yY":
        print("\nModifying the docs by default.")
        docs = True
    else:
        print("\nNot modifying the docs by default.")
        docs = False

    print("\nConverting these files:")
    for f in fname_list:
        print("   ", f)

    print_info("\nHit enter to begin the refactoring... ", end="")
    raw_input("")
    print()

    # Create a project.
    project = Project(project_dir)
    project.prefs.set("soa_followed_calls", SOA_FOLLOWED_CALLS)

    # Analyze the project.
    # Does this help refactoring?  See below for related discussion.
    # https://groups.google.com/forum/#!topic/rope-dev/1P8OADQ0DQ4
    print_info("Analyzing all the modules in the project, may be slow...")
    rope.base.libutils.analyze_modules(project) # Analyze all the modules.
    print_info("Finished the analysis.", sep="")
    print()

    for filename in fname_list:
        #experiment_with_scoping_classes(project, filename)
        print_banner("Python module name: " + filename_to_module_name(filename),
                     char="%", big=True)

        print_banner("Changing variables assigned in the code.")
        while change_assigned_variables:
            possible_changes = rope_iterate_worder(filename, assigned_vars=True)
            if not possible_changes:
                print("No more variable assignment changes.\n")
            if not rope_rename_refactor(project, filename, possible_changes, docs=docs):
                break

        print_banner("Changing function arguments which do not have defaults.")
        while change_function_and_method_arguments:
            possible_changes = rope_iterate_worder(filename, fun_arguments=True)
            if not possible_changes:
                print("No more function argument changes.\n")
            if not rope_rename_refactor(project, filename, possible_changes, docs=docs):
                break

        print_banner("Changing function and method names.")
        while change_function_and_method_names:
            possible_changes = rope_iterate_worder(filename, fun_name_defs=True)
            if not possible_changes:
                print("No more function and method name changes.\n")
            if not rope_rename_refactor(project, filename, possible_changes, docs=docs):
                break

        if not change_assigned_variables: # Redundant when that is also selected.
            print_banner("Changing function and method keywords.")
            while change_function_and_method_keywords:
                possible_changes = rope_iterate_worder(filename, fun_keywords=True)
                if not possible_changes:
                    print("No more function and method keyword changes.\n")
                if not rope_rename_refactor(project, filename, possible_changes, docs=docs):
                    break

        print_banner("Changing class names.")
        while change_class_names:
            possible_changes = rope_iterate_worder(filename, class_names=True)
            if not possible_changes:
                print("No more class name changes.\n")
            if not rope_rename_refactor(project, filename, possible_changes, docs=docs):
                break

    project.close()

if __name__ == "__main__":

    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nProgram exited by keyboard interrupt.", file=sys.stdout)
    finally:
        remove_rejected_change_magic_cookies(modified_modules_set)
        analyze_names_in_final_state([os.path.realpath(f) for f in fname_list])


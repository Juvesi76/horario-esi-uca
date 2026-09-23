# Copyright (C) 2026 Juvesi76
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

from horario_uca.select.conflicts import find_conflicts
from horario_uca.select.exams import default_exam_codes, find_exam_class_conflicts, find_exam_conflicts
from horario_uca.select.filter import filter_events, validate_selection

__all__ = [
    "default_exam_codes",
    "filter_events",
    "find_conflicts",
    "find_exam_class_conflicts",
    "find_exam_conflicts",
    "validate_selection",
]

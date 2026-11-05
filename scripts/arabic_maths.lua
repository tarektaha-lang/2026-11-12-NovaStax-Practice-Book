-- The numbers in a formula set right to left, for a book whose maths is in Arabic
-- notation. Loaded by latex.py into the preamble of every such paper and figure.
--
-- LuaTeX lays a formula out right to left when \mathdir is TRT: the atoms of the list
-- go down in reading order, first atom at the right, which is what an Arabic formula
-- wants of its operators, relations and letters. It is not what it wants of a number.
-- A number is a run of digit atoms and reads left to right whichever way the formula
-- runs, so laid out with the rest "90" comes out as "09". This turns each such run
-- round in the list before the engine lays it out, so that turned round again by the
-- engine it reads as written. A run is digits, with a decimal point, a thousands
-- mark, or a thin space (``\,``) between them; a per cent sign or a minus is not
-- part of it, and stands where the formula puts it, which in this notation is to
-- the left of the number. Left out of the run, ``100\,000`` is two numbers and TRT
-- prints each half backwards.
--
-- A mixed number is a number too: the integer and the fraction that follows it
-- ("5 11/12") read left to right as one unit. The paper's \frac is a \mathchoice,
-- so the fraction is a separate atom and would otherwise sit to the right of the
-- integer. The fraction is taken with the digit run and turned with it. A plus
-- between them is a different formula; a superscript on the last digit is not a
-- mixed number.
--
-- A repeating-decimal bar is a number too. \overline{16} is an over noad
-- (not an accent) whose nucleus is digits; \bar{6} is an accent. Either one
-- would otherwise split "0.16 rec" into "0." and a bar box. TeX then lays
-- the bar down first, so 0.\overline{16} prints as 16.0. The bar is taken
-- with the digit run and turned with it. \overline{AB} is a segment and
-- stays where it is.
--
-- Trailing dots are part of the same number: 0.4282828... and 0.\overline{16}.
-- Left out, they sit on the other side of the run, so the page prints
-- ...4282828.0. Two or three periods, or a single U+2026 ellipsis, are taken
-- with the run. A lone period is still a decimal or punctuation.
--
-- The scripts of a run's last digit — the degree sign of "90^\circ", the index of a
-- term — move to the digit that ends up last in the list, so that the sign still
-- closes the number in reading order rather than splitting it. A mixed number's
-- fraction is not a digit: its scripts stay on it.
--
-- When the book prints Arabic-Indic digits, the same pass reprints every Western
-- digit in a formula as its Arabic-Indic one, a per cent sign as the Arabic one, and
-- a decimal point or thousands comma as the Arabic marks. latex.py sets
-- arabic_maths_western_digits before this file is loaded when the book keeps 0-9:
-- then reprint is skipped, ASCII digits stay ASCII, and number runs still reverse.
-- Unset, the flag is Mashriq (Indic reprint), so an old TeX dump still works.
--
-- The paper's own Mashriq text arrives with its digits already Arabic-Indic
-- (latex.py rewrites them), but a figure's are computed: the ticks of an axis are
-- numbers pgfplots works out and prints as it goes, and a TikZ label is written 0-9
-- so that TikZ can read a coordinate beside it. Done on the atoms rather than on
-- the source, the digits that TikZ computes with are never touched and the digits
-- it prints all come out as the book prints them.
--
-- It works on the list of atoms rather than on the tokens because anything working on
-- tokens has to know which digits are printed and which are read by a macro — a "3" in
-- \mskip3mu, a "2" in \multicolumn{2} — and an atom is a digit that is about to be
-- printed and nothing else. The engine calls this filter for the outermost list of a
-- formula only, so the lists inside it — a fraction's numerator, a superscript, what
-- is under a radical — are walked from here.

local noad_id = node.id("noad")
local sub_mlist_id = node.id("sub_mlist")
local math_char_id = node.id("math_char")
local fraction_id = node.id("fraction")
local radical_id = node.id("radical")
local accent_id = node.id("accent")
local choice_id = node.id("choice")
local glue_id = node.id("glue")
local kern_id = node.id("kern")
local ORD, PUNCT = 0, 6

local DECIMAL, THOUSANDS, PERCENT = 0x66B, 0x66C, 0x66A
local WESTERN_DECIMAL, WESTERN_THOUSANDS = 0x2E, 0x2C

local western = arabic_maths_western_digits
if western == nil then western = false end

-- What a number is made of once Mashriq digits have been reprinted, and also the
-- Western digits a Maghreb book leaves as 0-9.
local numeric = {}
for c = 0x660, 0x669 do numeric[c] = true end
numeric[DECIMAL] = true
numeric[THOUSANDS] = true
if western then
  for c = 0x30, 0x39 do numeric[c] = true end
  numeric[WESTERN_DECIMAL] = true
  numeric[WESTERN_THOUSANDS] = true
end

-- What a Western character in a formula prints as in a Mashriq book.
local printed = {}
if not western then
  for d = 0, 9 do printed[0x30 + d] = 0x660 + d end
  printed[0x25] = PERCENT
end

local walk

local function char_of(n)
  local nucleus = n.id == noad_id and n.nucleus
  if nucleus and nucleus.id == math_char_id then return nucleus.char end
end

local function is_numeral(n)
  return n.subtype == ORD and numeric[char_of(n)] or false
end

-- A point or a comma between two digits is the mark inside a number; anywhere else it
-- is the punctuation it was typed as. Mashriq books reprint those marks as Arabic;
-- Maghreb books keep the ASCII characters and still take them into the run.
-- A colon between two digits is the mark inside a clock (``3:00`` or ``3{:}00``).
-- It stays ':' and is reversed with the digit run, so the hour stays left of the
-- minutes inside an RTL formula. ``{:}`` is an ORD whose nucleus is a one-item
-- sub-list holding only U+003A.
local function is_colon_char(c)
  -- U+003A and the math ratio colon U+2236 that LaTeX uses for `:`.
  return c == 0x3A or c == 0x2236
end

local function is_colon_atom(n)
  if n.id ~= noad_id then return false end
  if is_colon_char(char_of(n)) then return true end
  local nucleus = n.nucleus
  if not (nucleus and nucleus.id == sub_mlist_id and nucleus.list) then
    return false
  end
  local only = nucleus.list
  if only.next ~= nil or only.id ~= noad_id then return false end
  return is_colon_char(char_of(only))
end

local function is_mark(n, last)
  local c = char_of(n)
  if c == 0x2E and n.subtype == ORD then
    return western and WESTERN_DECIMAL or DECIMAL
  end
  if c == 0x2C and n.subtype == PUNCT and last then
    return western and WESTERN_THOUSANDS or THOUSANDS
  end
  if last and is_colon_atom(n) then
    return 0x3A
  end
  return nil
end

local function reprint(n)
  local nucleus = n.nucleus
  if nucleus and nucleus.id == math_char_id then
    local to = printed[nucleus.char]
    if to then nucleus.char = to end
  end
end

-- A superscript on the last *digit* ("90^\circ") closes the run. A decimal
-- mark is also a numeral after reprint, and may carry empty script fields;
-- those must not stop "0.\overline{16}" from taking the bar.
local function is_digit_atom(n)
  local c = char_of(n)
  if not (n.subtype == ORD and c) then return false end
  return (c >= 0x660 and c <= 0x669) or (c >= 0x30 and c <= 0x39)
end

local function scripted(n)
  return n.id == noad_id and is_digit_atom(n) and (n.sup ~= nil or n.sub ~= nil)
end

local function is_fraction(n)
  return n and (n.id == fraction_id or n.id == choice_id)
end

local function is_skip(n)
  return n and (n.id == glue_id or n.id == kern_id)
end

-- Digits a run may still see before reprint has walked an accent's nucleus:
-- Mashriq Arabic-Indic, Western 0-9, and either book's decimal / thousands mark.
local function is_digit_char(c)
  if not c then return false end
  if numeric[c] then return true end
  if c >= 0x30 and c <= 0x39 then return true end
  return c == WESTERN_DECIMAL or c == WESTERN_THOUSANDS
end

local function nucleus_is_digits(field)
  if not field then return false end
  if field.id == math_char_id then
    return is_digit_char(field.char)
  end
  if field.id ~= sub_mlist_id or not field.list then return false end
  local saw = false
  for x in node.traverse(field.list) do
    if x.id == noad_id then
      if not is_digit_char(char_of(x)) then return false end
      saw = true
    elseif x.id ~= glue_id and x.id ~= kern_id then
      return false
    end
  end
  return saw
end

-- LuaTeX noad subtype for primitive \overline. Looked up by name when the
-- engine publishes subtypes, so a later numbering does not silently miss it.
local over_sub = 11
if node.subtypes then
  local names = node.subtypes("noad")
  if names then
    if type(names.over) == "number" then
      over_sub = names.over
    else
      for id, name in pairs(names) do
        if name == "over" and type(id) == "number" then over_sub = id end
      end
    end
  end
end

-- \overline{16} is an over noad; \bar{6} is an accent. \overline{AB} is not digits.
local function is_repeating_bar(n)
  if not n then return false end
  if n.id == accent_id then
    return nucleus_is_digits(n.nucleus)
  end
  return n.id == noad_id and n.subtype == over_sub and nucleus_is_digits(n.nucleus)
end

local function continues_number(n)
  return (n.id == noad_id and is_numeral(n)) or is_repeating_bar(n)
end

local HORIZONTAL_ELLIPSIS = 0x2026

local function is_dot_atom(n)
  local c = char_of(n)
  return n and n.id == noad_id and (c == 0x2E or c == DECIMAL or c == HORIZONTAL_ELLIPSIS)
end

-- Dots that close a repeating decimal: ..., or one ellipsis character.
-- A single period is a decimal or punctuation and is left alone.
local function trailing_ellipsis(n)
  if not n then return nil end
  if char_of(n) == HORIZONTAL_ELLIPSIS then return n end
  if not is_dot_atom(n) then return nil end
  local last, count, p = n, 0, n
  while p and is_dot_atom(p) and count < 3 do
    last = p
    count = count + 1
    p = p.next
  end
  if count >= 2 then return last end
  return nil
end

-- The atoms from first to last, inclusive, in the other order and in the same place.
-- Scripts move only on a digit run: the degree on "90^\circ" belongs to the last
-- digit, which is no longer last after the reverse. A mixed number ends on a
-- fraction, and that fraction keeps whatever scripts it had.
local function reversed(head, first, last)
  local run = {}
  local n = first
  while true do
    run[#run + 1] = n
    if n == last then break end
    n = n.next
  end
  if #run < 2 then return head end
  local steal = last.id == noad_id and last.subtype ~= over_sub
  local sup, sub
  if steal then
    sup, sub = last.sup, last.sub
    last.sup, last.sub = nil, nil
  end
  local after = last.next
  for _, m in ipairs(run) do head = node.remove(head, m) end
  for i = #run, 1, -1 do
    if after then
      head = node.insert_before(head, after, run[i])
    else
      head = node.insert_after(head, node.tail(head), run[i])
    end
  end
  if steal then
    first.sup, first.sub = sup, sub
  end
  return head
end

local function numbers(head)
  for n in node.traverse(head) do
    if n.id == noad_id then reprint(n) end
  end
  local n = head
  while n do
    if n.id == noad_id and is_numeral(n) then
      local first, last = n, n
      local m = n.next
      while m and not scripted(last) do
        -- Over noads are still noads; take the bar before the generic
        -- "not a digit, not a mark" break.
        if is_repeating_bar(m) then
          last = m
        elseif m.id == noad_id then
          local mark = is_mark(m, last)
          if is_numeral(m) then
            last = m
          elseif mark and m.next and continues_number(m.next) then
            if mark ~= 0x3A and m.nucleus and m.nucleus.id == math_char_id then
              m.nucleus.char = mark
            end
            m.subtype = ORD
            last = m
          else
            break
          end
        elseif is_skip(m) then
          -- Thin space between digits is thousands grouping (``100\,000``).
          -- A skip after the number still breaks, as it did.
          local tail = m.next
          while is_skip(tail) do
            tail = tail.next
          end
          if tail and continues_number(tail) then
            last = m
          else
            break
          end
        else
          break
        end
        m = m.next
      end
      if not scripted(last) then
        local tail = last.next
        while is_skip(tail) do
          tail = tail.next
        end
        local dots = trailing_ellipsis(tail)
        if dots then
          last = dots
        elseif is_fraction(tail) then
          last = tail
        end
      end
      local after = last.next
      head = reversed(head, first, last)
      n = after
    else
      n = n.next
    end
  end
  return head
end

local function inner(f)
  if f and f.id == sub_mlist_id and f.list then f.list = walk(f.list) end
end

walk = function(head)
  head = numbers(head)
  for n in node.traverse(head) do
    if n.id == noad_id or n.id == radical_id or n.id == accent_id then
      inner(n.nucleus)
      inner(n.sup)
      inner(n.sub)
      if n.id == radical_id then inner(n.degree) end
    elseif n.id == fraction_id then
      inner(n.num)
      inner(n.denom)
    elseif n.id == choice_id then
      -- \mathchoice, which the paper's \frac is built on: four lists, one per size,
      -- held as the lists themselves rather than wrapped as a sub_mlist.
      for _, size in ipairs({"display", "text", "script", "scriptscript"}) do
        if n[size] then n[size] = walk(n[size]) end
      end
    end
  end
  return head
end

luatexbase.add_to_callback("pre_mlist_to_hlist_filter", function(head)
  return walk(head)
end, "arabic maths numbers")

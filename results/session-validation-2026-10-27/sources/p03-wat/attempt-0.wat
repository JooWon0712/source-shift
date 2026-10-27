local.get $a
local.get $b
i32.lt_s
if (result i32)
  local.get $b
else
  local.get $a
  local.get $c
  i32.gt_s
  if (result i32)
    local.get $c
  else
    local.get $a
  end
end

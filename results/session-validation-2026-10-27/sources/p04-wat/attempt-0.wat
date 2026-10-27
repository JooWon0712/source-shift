local.get $a
i32.const 0
i32.lt_s
if (result i32)
  i32.const 0
  local.get $a
  i32.sub
else
  local.get $a
end

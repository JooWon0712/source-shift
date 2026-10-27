(local $d i32)
local.get $a
i32.const 2
i32.lt_s
if
  i32.const 0
  return
end
i32.const 2
local.set $d
block $done
  loop $trial
    local.get $d
    local.get $d
    i32.mul
    local.get $a
    i32.gt_s
    br_if $done
    local.get $a
    local.get $d
    i32.rem_s
    i32.eqz
    if
      i32.const 0
      return
    end
    local.get $d
    i32.const 1
    i32.add
    local.set $d
    br $trial
  end
end
i32.const 1

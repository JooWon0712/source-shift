(local $digits i32)
i32.const 1
local.set $digits
block $done
  loop $again
    local.get $a
    i32.const 10
    i32.lt_s
    br_if $done
    local.get $a
    i32.const 10
    i32.div_s
    local.set $a
    local.get $digits
    i32.const 1
    i32.add
    local.set $digits
    br $again
  end
end
local.get $digits

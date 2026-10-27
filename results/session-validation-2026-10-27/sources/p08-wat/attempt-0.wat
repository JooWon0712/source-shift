(local $remainder i32)
block $done
  loop $gcd
    local.get $b
    i32.eqz
    br_if $done
    local.get $a
    local.get $b
    i32.rem_s
    local.set $remainder
    local.get $b
    local.set $a
    local.get $remainder
    local.set $b
    br $gcd
  end
end
local.get $a

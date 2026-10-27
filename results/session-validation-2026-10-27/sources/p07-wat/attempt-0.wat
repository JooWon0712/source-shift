(local $r i32)
(local $i i32)
i32.const 1
local.set $r
i32.const 2
local.set $i
block $done
  loop $next
    local.get $i
    local.get $a
    i32.gt_s
    br_if $done
    local.get $r
    local.get $i
    i32.mul
    local.set $r
    local.get $i
    i32.const 1
    i32.add
    local.set $i
    br $next
  end
end
local.get $r

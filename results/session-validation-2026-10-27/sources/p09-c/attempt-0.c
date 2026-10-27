int32_t digits = 1;
while (a >= 10) {
    a /= 10;
    digits += 1;
}
return digits;

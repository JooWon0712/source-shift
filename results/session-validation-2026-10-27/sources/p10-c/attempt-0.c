if (a < 2) {
    return 0;
}
for (int32_t divisor = 2; divisor * divisor <= a; ++divisor) {
    if (a % divisor == 0) {
        return 0;
    }
}
return 1;

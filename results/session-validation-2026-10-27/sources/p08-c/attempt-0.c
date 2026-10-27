while (b != 0) {
    int32_t remainder = a % b;
    a = b;
    b = remainder;
}
return a;

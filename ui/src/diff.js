// Line diff (LCS) between two assembly listings, on whitespace-normalized lines.
const norm = (l) => l.trim().toLowerCase().replace(/\s+/g, " ");
/** For each line of `a` and of `b`: whether it is shared with the other listing. */
export function diffLines(a, b) {
    const A = a.map(norm);
    const B = b.map(norm);
    const dp = Array.from({ length: A.length + 1 }, () => new Array(B.length + 1).fill(0));
    for (let i = A.length - 1; i >= 0; i--) {
        for (let j = B.length - 1; j >= 0; j--) {
            dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
        }
    }
    const ma = new Array(A.length).fill("changed");
    const mb = new Array(B.length).fill("changed");
    let i = 0;
    let j = 0;
    while (i < A.length && j < B.length) {
        if (A[i] === B[j]) {
            ma[i++] = "same";
            mb[j++] = "same";
        }
        else if (dp[i + 1][j] >= dp[i][j + 1]) {
            i++;
        }
        else {
            j++;
        }
    }
    return { a: ma, b: mb };
}

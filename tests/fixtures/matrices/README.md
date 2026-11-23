# One small matrix, in every format Strata reads

Each file here records the same invented excavation, so the tests can check
that every reader gives the same graph:

| unit | what it is |
|------|------------|
| 1 | topsoil |
| 2 | wall |
| 3 | pit cut |
| 4 | pit fill |
| 5 | floor, north of the wall |
| 6 | floor, south of the wall (the same floor as 5) |
| 7 | natural |

Statements: 1 above 2; 1 above 4; 4 fills 3; 3 cuts 5; 2 abuts 5; 5 same as 6;
5 above 7; 6 above 7; 2 above 7.

Written for Strata's tests (CC0).

-- Copyright 2026 Katteli Inc.
--
-- Licensed under the Apache License, Version 2.0 (the "License");
-- you may not use this file except in compliance with the License.
-- You may obtain a copy of the License at
--
--     http://www.apache.org/licenses/LICENSE-2.0
--
-- Unless required by applicable law or agreed to in writing, software
-- distributed under the License is distributed on an "AS IS" BASIS,
-- WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
-- See the License for the specific language governing permissions and
-- limitations under the License.

CREATE TABLE orders (id int, customer text, amount numeric);
INSERT INTO orders VALUES (1, 'ada', 30), (2, 'grace', 45), (3, 'ada', 25);
SELECT customer, sum(amount) AS total FROM orders GROUP BY customer ORDER BY customer;

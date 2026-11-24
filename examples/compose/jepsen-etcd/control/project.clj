;; Copyright 2026 Katteli Inc.
;;
;; Licensed under the Apache License, Version 2.0 (the "License");
;; you may not use this file except in compliance with the License.
;; You may obtain a copy of the License at
;;
;;     http://www.apache.org/licenses/LICENSE-2.0
;;
;; Unless required by applicable law or agreed to in writing, software
;; distributed under the License is distributed on an "AS IS" BASIS,
;; WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
;; See the License for the specific language governing permissions and
;; limitations under the License.

(defproject etcd-machine "0.1.0"
  :description "A Jepsen test of etcd, run in TestFlows Machine"
  :dependencies [[org.clojure/clojure "1.12.0"]
                 [jepsen "0.3.15"]
                 [clj-http "3.13.0"]
                 [cheshire "5.13.0"]]
  :main jepsen.etcd-machine
  :uberjar-name "etcd-machine.jar"
  :profiles {:uberjar {:aot :all}})

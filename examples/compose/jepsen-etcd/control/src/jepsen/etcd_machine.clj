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

(ns jepsen.etcd-machine
  "A linearizable compare-and-set register on a three-node etcd cluster,
  with Jepsen's random-halves partitions. Clients talk to etcd's v3 JSON
  gateway over HTTP."
  (:gen-class)
  (:require [cheshire.core :as json]
            [clj-http.client :as http]
            [clojure.string :as str]
            [clojure.tools.logging :refer [info]]
            [jepsen [checker :as checker]
                    [cli :as cli]
                    [client :as client]
                    [control :as c]
                    [db :as db]
                    [generator :as gen]
                    [nemesis :as nemesis]
                    [os :as os]
                    [tests :as tests]]
            [jepsen.control.util :as cu]
            [knossos.model :as model])
  (:import (java.util Base64)))

(def dir "/opt/etcd")
(def binary (str dir "/etcd"))
(def data-dir "/var/lib/etcd-data")
(def logfile "/var/log/etcd.log")
(def pidfile "/var/run/etcd.pid")
(def register-key "r")

(defn url [host port] (str "http://" host ":" port))

(defn initial-cluster [test]
  (->> (:nodes test)
       (map #(str % "=" (url % 2380)))
       (str/join ",")))

(defn etcd []
  (reify
    db/DB
    (setup! [_ test node]
      (info node "starting etcd")
      (c/su
        (cu/start-daemon!
          {:logfile logfile :pidfile pidfile :chdir dir}
          binary
          :--name node
          :--data-dir data-dir
          :--listen-peer-urls (url "0.0.0.0" 2380)
          :--listen-client-urls (url "0.0.0.0" 2379)
          :--advertise-client-urls (url node 2379)
          :--initial-advertise-peer-urls (url node 2380)
          :--initial-cluster-state :new
          :--initial-cluster (initial-cluster test)))
      ;; etcd answers once the three nodes have elected a leader.
      (Thread/sleep 10000))

    (teardown! [_ test node]
      (info node "stopping etcd")
      (c/su
        (cu/stop-daemon! binary pidfile)
        (c/exec :rm :-rf data-dir)))

    db/LogFiles
    (log-files [_ test node] [logfile])))

(defn b64 [x] (.encodeToString (Base64/getEncoder) (.getBytes (str x) "UTF-8")))
(defn unb64 [^String s] (String. (.decode (Base64/getDecoder) s) "UTF-8"))

(defn post [node path body]
  (-> (http/post (str (url node 2379) path)
                 {:body               (json/generate-string body)
                  :content-type       :json
                  :socket-timeout     5000
                  :connection-timeout 5000})
      :body
      (json/parse-string true)))

(defn read-register
  "Reads the register. A serializable read is answered by the node from its
  own copy, without the leader, so a node cut off from the majority answers
  with a value that may be stale."
  [node serializable?]
  (some-> (post node "/v3/kv/range" {:key (b64 register-key)
                                     :serializable serializable?})
          :kvs first :value unb64 parse-long))

(defn write-register! [node v]
  (post node "/v3/kv/put" {:key (b64 register-key) :value (b64 v)}))

(defn cas-register! [node old new]
  (-> (post node "/v3/kv/txn"
            {:compare [{:key (b64 register-key) :target "VALUE"
                        :result "EQUAL" :value (b64 old)}]
             :success [{:requestPut {:key (b64 register-key) :value (b64 new)}}]})
      :succeeded
      boolean))

(defrecord Client [node]
  client/Client
  (open! [this test node] (assoc this :node node))
  (setup! [this test])
  (invoke! [this test op]
    ;; A read that fails changed nothing. A write or cas that times out may
    ;; or may not have happened, so it is :info, indeterminate.
    (let [failed (if (= :read (:f op)) :fail :info)]
      (try
        (case (:f op)
          ;; The final read is linearizable in either mode, so a stale value
          ;; is one read while the network was partitioned.
          :read  (assoc op :type :ok
                        :value (read-register node (and (not (:final? op))
                                                        (= "serializable" (:reads test)))))
          :write (do (write-register! node (:value op)) (assoc op :type :ok))
          :cas   (let [[old new] (:value op)]
                   (assoc op :type (if (cas-register! node old new) :ok :fail))))
        (catch java.net.ConnectException _
          (assoc op :type :fail :error :connect-refused))
        (catch java.net.SocketTimeoutException _
          (assoc op :type failed :error :timeout))
        (catch Exception e
          (assoc op :type failed :error (str e))))))
  (teardown! [this test])
  (close! [this test]))

(defn r [_ _] {:type :invoke :f :read :value nil})
(defn w [_ _] {:type :invoke :f :write :value (rand-int 5)})
(defn cas [_ _] {:type :invoke :f :cas :value [(rand-int 5) (rand-int 5)]})

(defn etcd-test [opts]
  (merge tests/noop-test
         opts
         {:name      "etcd-machine"
          :os        os/noop
          :db        (etcd)
          :client    (Client. nil)
          :nemesis   (nemesis/partition-random-halves)
          :checker   (checker/compose
                       {:stats  (checker/stats)
                        :linear (checker/linearizable
                                  {:model (model/cas-register)
                                   :algorithm :linear})})
          :generator (gen/phases
                       (->> (gen/mix [r w cas])
                            (gen/stagger 1/50)
                            (gen/nemesis
                              (cycle [(gen/sleep 5)
                                      {:type :info :f :start}
                                      (gen/sleep 5)
                                      {:type :info :f :stop}]))
                            (gen/time-limit (:time-limit opts)))
                       (gen/log "Healing the network")
                       (gen/nemesis (gen/once {:type :info :f :stop}))
                       (gen/sleep 5)
                       (gen/clients (gen/once {:type :invoke :f :read :value nil
                                               :final? true})))}))

(def opt-spec
  [[nil "--reads MODE"
    "linearizable, or serializable to read stale values under a partition"
    :default "linearizable"
    :validate [#{"linearizable" "serializable"}
               "Must be linearizable or serializable"]]])

(defn -main [& args]
  (cli/run! (merge (cli/single-test-cmd {:test-fn etcd-test :opt-spec opt-spec})
                   (cli/serve-cmd))
            args))

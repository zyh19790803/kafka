# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import time
from ducktape.tests.test import Test
from ducktape.utils.util import wait_until
from kafkatest.services.kafka import KafkaService
from kafkatest.services.streams import StreamsBrokerDownResilienceService
from kafkatest.services.verifiable_consumer import VerifiableConsumer
from kafkatest.services.verifiable_producer import VerifiableProducer
from kafkatest.services.zookeeper import ZookeeperService


class StreamsBrokerDownResilience(Test):
    """
    This test validates that Streams is resilient to a broker
    being down longer than specified timeouts in configs
    """

    inputTopic = "streamsResilienceSource"
    outputTopic = "streamsResilienceSink"
    num_messages = 5

    def __init__(self, test_context):
        super(StreamsBrokerDownResilience, self).__init__(test_context=test_context)
        self.zk = ZookeeperService(test_context, num_nodes=1)
        self.kafka = KafkaService(test_context,
                                  num_nodes=1,
                                  zk=self.zk,
                                  topics={
                                      self.inputTopic: {'partitions': 1, 'replication-factor': 1},
                                      self.outputTopic: {'partitions': 1, 'replication-factor': 1}
                                  })

    def get_consumer(self):
        return VerifiableConsumer(self.test_context,
                                  1,
                                  self.kafka,
                                  self.outputTopic,
                                  "stream-broker-resilience-verify-consumer",
                                  max_messages=self.num_messages)

    def get_producer(self):
        return VerifiableProducer(self.test_context,
                                  1,
                                  self.kafka,
                                  self.inputTopic,
                                  max_messages=self.num_messages,
                                  acks=1)

    def assert_produce_consume(self, test_state):
        producer = self.get_producer()
        producer.start()

        wait_until(lambda: producer.num_acked > 0,
                   timeout_sec=30,
                   err_msg="At %s failed to send messages " % test_state)

        consumer = self.get_consumer()
        consumer.start()

        wait_until(lambda: consumer.total_consumed() > 0,
                   timeout_sec=60,
                   err_msg="At %s streams did not process messages in 60 seconds " % test_state)

    def setUp(self):
        self.zk.start()

    def test_streams_resilient_to_broker_down(self):
        self.kafka.start()

        # Consumer max.poll.interval > min(max.block.ms, ((retries + 1) * request.timeout)
        consumer_poll_ms = "consumer.max.poll.interval.ms=50000"
        retries_config = "producer.retries=2"
        request_timeout = "producer.request.timeout.ms=15000"
        max_block_ms = "producer.max.block.ms=30000"

        # Broker should be down over 2x of retries * timeout ms
        # So with (2 * 15000) = 30 seconds, we'll set downtime to 70 seconds
        broker_down_time_in_seconds = 70

        # java code expects configs in key=value,key=value format
        updated_configs = consumer_poll_ms + "," + retries_config + "," + request_timeout + "," + max_block_ms

        processor = StreamsBrokerDownResilienceService(self.test_context, self.kafka, updated_configs)
        processor.start()

        # until KIP-91 is merged we'll only send 5 messages to assert Kafka Streams is running before taking the broker down
        # After KIP-91 is merged we'll continue to send messages the duration of the test
        self.assert_produce_consume("before_broker_stop")

        node = self.kafka.leader(self.inputTopic)

        self.kafka.stop_node(node)

        time.sleep(broker_down_time_in_seconds)

        self.kafka.start_node(node)

        self.assert_produce_consume("after_broker_stop")

        self.kafka.stop()

import unittest
import numpy as np
from local.speech import intervals, conversation_spans, merge_utterances, split_turn, RATE

class SpeakerTimelineTests(unittest.TestCase):
    def test_overlap_is_not_assigned_to_one_speaker(self):
        turns=[{'start':0,'end':2,'speakerId':'spk_0'},{'start':1,'end':3,'speakerId':'spk_1'}]
        spans=intervals(turns,3)
        self.assertEqual([(s['start'],s['end']) for s in spans],[(0,1),(1,2),(2,3)])
        self.assertEqual(spans[1]['speakerIds'],['spk_0','spk_1'])
        self.assertTrue(spans[1]['overlap'])
        self.assertEqual(sum(s['end']-s['start'] for s in spans),3)

    def test_same_speaker_overlapping_windows_are_not_two_people(self):
        spans=intervals([{'start':0,'end':2,'speakerId':'spk_0'},{'start':1,'end':3,'speakerId':'spk_0'}],3)
        self.assertEqual(len(spans),1)
        self.assertFalse(spans[0]['overlap'])

    def test_long_turn_splits_without_gaps_or_duplicates(self):
        values=np.ones(RATE*31,dtype=np.float32)*.05
        chunks=list(split_turn(values,0,31))
        self.assertEqual(chunks[0][0],0)
        self.assertEqual(chunks[-1][1],len(values))
        self.assertTrue(all(b-a<=30*RATE and b>a for a,b in chunks))
        self.assertTrue(all(chunks[i][1]==chunks[i+1][0] for i in range(len(chunks)-1)))

    def test_silence_gaps_are_preserved_in_timestamps(self):
        spans=intervals([{'start':1,'end':2,'speakerId':'spk_0'},{'start':5,'end':6,'speakerId':'spk_1'}],8)
        self.assertEqual([(s['start'],s['end']) for s in spans],[(1,2),(5,6)])

    def test_short_pauses_do_not_fragment_same_person_before_asr(self):
        spans=intervals([{'start':0,'end':2,'speakerId':'spk_0'},
                        {'start':2.3,'end':5,'speakerId':'spk_0'},
                        {'start':5.5,'end':9,'speakerId':'spk_0'},
                        {'start':10,'end':12,'speakerId':'spk_1'}],12)
        grouped=conversation_spans(spans)
        self.assertEqual([(s['start'],s['end']) for s in grouped],[(0,9),(10,12)])

    def test_merge_never_hides_speaker_switch_or_overlap(self):
        turns=[{'start':0,'end':5,'speakerId':'spk_0'},
               {'start':2,'end':3,'speakerId':'spk_1'},
               {'start':5,'end':6,'speakerId':'spk_1'}]
        grouped=conversation_spans(intervals(turns,6))
        self.assertEqual(len(grouped),4)
        self.assertTrue(grouped[1]['overlap'])
        self.assertEqual(grouped[2]['speakerIds'],['spk_0'])

    def test_long_silence_remains_a_turn_boundary(self):
        spans=intervals([{'start':0,'end':2,'speakerId':'spk_0'},
                        {'start':7,'end':9,'speakerId':'spk_0'}],10)
        self.assertEqual(len(conversation_spans(spans)),2)

    def test_internal_windows_are_one_visible_utterance_without_rewriting(self):
        source=[{'id':0,'start':0,'end':29,'text':'请问几位？','speakerIds':['spk_0'],'overlap':False},
                {'id':1,'start':29,'end':40,'text':'有忌口吗？','speakerIds':['spk_0'],'overlap':False},
                {'id':2,'start':40,'end':41,'text':'两位。','speakerIds':['spk_1'],'overlap':False}]
        merged=merge_utterances(source)
        self.assertEqual([s['text'] for s in merged],['请问几位？有忌口吗？','两位。'])
        self.assertEqual([s['id'] for s in merged],[0,1])
        self.assertEqual(merged[0]['end'],40)
        self.assertEqual(source[0]['text'],'请问几位？')

if __name__=='__main__':
    unittest.main()
